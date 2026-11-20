"""Custom plant library for storing user-defined plant species."""

import contextlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.storage_paths import get_app_data_dir as get_app_data_dir


class PlantLibrary:
    """Manages a library of custom plant species.

    Stores user-defined plants in a JSON file in the application data directory.
    Plants are indexed by a unique ID for easy retrieval and deletion.
    """

    LIBRARY_FILENAME = "custom_plants.json"

    def __init__(self) -> None:
        """Initialize the plant library."""
        self._plants: dict[str, PlantSpeciesData] = {}
        self._library_path = get_app_data_dir() / self.LIBRARY_FILENAME
        self._load()

    def _load(self) -> None:
        """Load custom plants from the library file."""
        if not self._library_path.exists():
            return

        try:
            with open(self._library_path, encoding="utf-8") as f:
                data = json.load(f)

            # Load plants from the file
            for plant_id, plant_data in data.get("plants", {}).items():
                try:
                    self._plants[plant_id] = PlantSpeciesData.from_dict(plant_data)
                except (KeyError, ValueError):
                    # Skip invalid entries
                    continue

        except (json.JSONDecodeError, OSError):
            # If file is corrupted, start with empty library
            self._plants = {}

    def _save(self) -> bool:
        """Save custom plants to the library file."""
        data = {
            "version": "1.0",
            "plants": {
                plant_id: plant.to_dict() for plant_id, plant in self._plants.items()
            },
        }

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._library_path.parent,
                suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2)
            os.replace(temporary, self._library_path)
        except OSError:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
            return False
        return True

    def add_plant(self, plant: PlantSpeciesData, *, require_saved: bool = False) -> str:
        """Add a custom plant to the library.

        Args:
            plant: The plant species data to add
            require_saved: Raise OSError and roll back if the store cannot be written

        Returns:
            The unique ID assigned to the plant
        """
        # Ensure it's marked as custom
        plant.data_source = "custom"
        if not plant.source_id:
            plant.source_id = str(uuid.uuid4())

        plant_id = plant.source_id
        previous = self._plants.get(plant_id)
        self._plants[plant_id] = plant
        if not self._save() and require_saved:
            if previous is None:
                self._plants.pop(plant_id)
            else:
                self._plants[plant_id] = previous
            raise OSError("Could not write the custom plant library")
        return plant_id

    def update_plant(self, plant_id: str, plant: PlantSpeciesData) -> bool:
        """Update an existing plant in the library.

        Args:
            plant_id: The ID of the plant to update
            plant: The updated plant data

        Returns:
            True if the plant was found and saved, False otherwise
        """
        previous = self._plants.get(plant_id)
        if previous is None:
            return False

        plant.data_source = "custom"
        plant.source_id = plant_id
        self._plants[plant_id] = plant
        if self._save():
            return True
        self._plants[plant_id] = previous
        return False

    def remove_plant(self, plant_id: str) -> bool:
        """Remove a plant from the library.

        Args:
            plant_id: The ID of the plant to remove

        Returns:
            True if the plant was found and removed, False otherwise
        """
        if plant_id not in self._plants:
            return False

        del self._plants[plant_id]
        self._save()
        return True

    def get_plant(self, plant_id: str) -> PlantSpeciesData | None:
        """Get a plant by its ID.

        Args:
            plant_id: The ID of the plant to retrieve

        Returns:
            The plant data, or None if not found
        """
        return self._plants.get(plant_id)

    def get_all_plants(self) -> list[tuple[str, PlantSpeciesData]]:
        """Get all plants in the library with their IDs.

        Returns:
            List of tuples (plant_id, plant_data) for all custom plants
        """
        return list(self._plants.items())

    def search_plants(self, query: str) -> list[PlantSpeciesData]:
        """Search for plants by name.

        Args:
            query: Search query (matches scientific name, common name, or family)

        Returns:
            List of matching plants
        """
        query_lower = query.lower()
        results = []

        for plant in self._plants.values():
            if (
                query_lower in plant.scientific_name.lower()
                or query_lower in plant.common_name.lower()
                or query_lower in plant.home_name.lower()
                or query_lower in plant.family.lower()
            ):
                results.append(plant)

        return results

    @property
    def count(self) -> int:
        """Get the number of plants in the library."""
        return len(self._plants)

    def to_dict(self) -> dict[str, Any]:
        """Export the library as a dictionary for project file inclusion.

        Returns:
            Dictionary representation of the library
        """
        return {
            plant_id: plant.to_dict() for plant_id, plant in self._plants.items()
        }

    def import_from_dict(self, data: dict[str, Any]) -> int:
        """Import plants from a dictionary (e.g., from a project file).

        Only imports plants that don't already exist in the library.

        Args:
            data: Dictionary mapping plant IDs to plant data

        Returns:
            Number of plants imported
        """
        imported = 0
        for plant_id, plant_data in data.items():
            if plant_id not in self._plants:
                try:
                    plant = PlantSpeciesData.from_dict(plant_data)
                    plant.data_source = "custom"
                    plant.source_id = plant_id
                    self._plants[plant_id] = plant
                    imported += 1
                except (KeyError, ValueError):
                    continue

        if imported > 0:
            self._save()

        return imported


# Singleton instance for app-wide access
_library_instance: PlantLibrary | None = None


def get_plant_library() -> PlantLibrary:
    """Get the singleton plant library instance.

    Returns:
        The global PlantLibrary instance
    """
    global _library_instance
    if _library_instance is None:
        _library_instance = PlantLibrary()
    return _library_instance
