import numpy as np
from scipy.ndimage import map_coordinates
from scipy.spatial import distance_matrix


from .types import (
    MaskImage,
    IntensityImage,
)

from .types import IntCoodArray, PerpendicularProfile


def normalize(v: np.ndarray):
    return v / np.linalg.norm(v) if np.linalg.norm(v) != 0 else v


def order_skeleton_points(skeleton: MaskImage) -> IntCoodArray:
    coords = np.column_stack(np.where(skeleton))  # (y, x)
    print(coords)
    # Step 1: Compute pairwise distances
    dist = distance_matrix(coords, coords)

    # Step 2: Find endpoints (points with only one neighbor)
    neighbors = dist == 1
    degrees = neighbors.sum(axis=1)
    endpoint_indices = np.where(degrees == 1)[0]

    if len(endpoint_indices) == 0:
        raise ValueError("No endpoints found. Is your skeleton branched or a loop?")
    elif len(endpoint_indices) > 2:
        print("Warning: More than 2 endpoints found. Choosing first one.")

    # Step 3: Greedy walk from an endpoint
    start_idx = endpoint_indices[0]
    ordered = [start_idx]
    visited = set(ordered)

    while len(ordered) < len(coords):
        current = ordered[-1]
        # Find unvisited neighbors
        next_candidates = np.where(dist[current] == 1)[0]
        next_unvisited = [idx for idx in next_candidates if idx not in visited]
        if not next_unvisited:
            break
        next_idx = next_unvisited[0]
        ordered.append(next_idx)
        visited.add(next_idx)

    return coords[ordered]


def get_ordered_perpendicular_profiles(
    image: IntensityImage, skeleton: MaskImage, line_width: int
) -> list[PerpendicularProfile]:
    rows, cols = np.where(skeleton)
    profiles: list[PerpendicularProfile] = []

    sorted_ndx = np.argsort(rows)

    for i in range(1, len(sorted_ndx) - 1):
        ndx0: int = sorted_ndx[i - 1]
        ndx1: int = sorted_ndx[i]
        ndx2: int = sorted_ndx[i + 1]

        row0, col0 = rows[ndx0], cols[ndx0]
        row1, col1 = rows[ndx1], cols[ndx1]
        row2, col2 = rows[ndx2], cols[ndx2]

        drow, dcol = (row2 - row0, col2 - col0)
        tangent = normalize(np.array([drow, dcol]))

        # Perpendicular direction
        normal = np.array([-tangent[1], tangent[0]])

        # Line coordinates along the perpendicular direction
        half_width = line_width // 2
        line_coords = [
            (row1 + normal[0] * d, col1 + normal[1] * d)
            for d in range(-half_width, half_width + 1)
        ]
        line_coords = np.array(line_coords).T  # shape (2, N)

        # Sample intensity using bilinear interpolation
        intensities = map_coordinates(image, line_coords, order=1, mode="reflect")
        profiles.append(
            {
                "coordinates": (row1, col1),
                "intensities": intensities,
            }
        )

    return profiles
