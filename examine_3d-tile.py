# Processes an OGC 3D Tileset to calculate various statistics about the mesh.
#
# Statistical data that for each 3d tile mesh:
# - screen space error from the tileset.json file,
# - polygon edge length average, median and standard deviation, 
# - total polygon count,
# - texel size average, median and standard deviation, 
# - texture utilization,
# - texture image size,
# - 
# The `process_glb_file` function takes a path to a GLB file, imports the mesh into Blender, calculates the statistics, and returns a dictionary with the results.
# The `load_tileset` function loads a tileset JSON file, processes the tile structure, and returns a list of tile information dictionaries, including the tile URL, parent ID, LOD level, and screen space error.
# The `process_tile_parallel` function is used to process each tile in parallel using a multiprocessing pool.
# The `write_results_to_csv` function writes the calculated statistics for all tiles to a CSV file.

import bpy
import bmesh
import numpy as np
from mathutils import Vector
import sys
import os
import re
import json
import csv
import time
import multiprocessing
import contextlib
import io

script_start_time = time.time()

# Global counters for unique numeric IDs
tile_id_counter = 0

### Misc 

@contextlib.contextmanager
def suppress_output():
    with open(os.devnull, 'w') as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = devnull
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

### Graphic processing

def point_in_triangle_uv(p, triangle):
    def sign(p1, p2, p3):
        return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)
    
    d1 = sign(p, triangle[0], triangle[1])
    d2 = sign(p, triangle[1], triangle[2])
    d3 = sign(p, triangle[2], triangle[0])
    
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    
    return not (has_neg and has_pos)

def calculate_texel_sizes_and_utilization(obj):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    
    uv_layer = bm.loops.layers.uv.active
    if not uv_layer:
        print("No active UV layer found")
        return [], 0
    
    # Get texture information
    image = None
    if obj.active_material and obj.active_material.node_tree:
        for node in obj.active_material.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                image = node.image
                break
    
    if not image:
        print("No valid texture found.")
        return [], 0
    
    image_width, image_height = image.size
    #grid_resolution = 512  # Set the grid_resolution of the grid to 1024x1024
    #grid_resolution = max(image_width, image_height)  # Use the larger dimension for a square grid
    fraction = 0.5  # Set the fraction of the image to sample
    grid_resolution = int(max(image_width, image_height) * fraction)  # Use the larger dimension for a square grid

    grid = [[0 for _ in range(grid_resolution)] for _ in range(grid_resolution)]
    total_pixels = grid_resolution * grid_resolution
    texel_sizes = []
    
    for face in bm.faces:
        face_area = face.calc_area()
        uv_coords = [l[uv_layer].uv for l in face.loops]
        
        # Calculate bounding box of the face in UV space
        min_u = min(uv.x for uv in uv_coords)
        max_u = max(uv.x for uv in uv_coords)
        min_v = min(uv.y for uv in uv_coords)
        max_v = max(uv.y for uv in uv_coords)
        
        pixel_count = 0
        # Sample points within the bounding box
        for u in range(int(min_u * grid_resolution), min(int(max_u * grid_resolution) + 1, grid_resolution)):
            for v in range(int(min_v * grid_resolution), min(int(max_v * grid_resolution) + 1, grid_resolution)):
                point = Vector((u / grid_resolution, v / grid_resolution))
                if point_in_triangle_uv(point, uv_coords):
                    if grid[v][u] == 0:
                        grid[v][u] = 1
                        pixel_count += 1
        
        if pixel_count > 0:
            # Calculate texel size based on the actual image grid_resolution
            texel_size = (face_area / (pixel_count * (image_width / grid_resolution) * (image_height / grid_resolution))) ** 0.5
            texel_sizes.append(texel_size)
    
    used_pixels = sum(sum(row) for row in grid)
    texture_utilization = used_pixels / total_pixels
    
    bm.free()
    return texel_sizes, texture_utilization, image_width

def calculate_statistics(obj):
    function_start_time = time.time()
    
    texel_start_time = time.time()
    sizes,texture_utilization, texture_width = calculate_texel_sizes_and_utilization(obj)
    texel_end_time = time.time()
    
    if not sizes:
        return None
    
    # Texel size statistics
    avg_texel_size = np.mean(sizes)
    median_texel_size = np.median(sizes)
    std_dev_texel_size = np.std(sizes)
    
    # Polygon edge statistics
    polygon_start_time = time.time()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    edge_lengths = [edge.calc_length() for edge in bm.edges]
    avg_polygon_edge_length = np.mean(edge_lengths)
    median_polygon_edge_length = np.median(edge_lengths)
    std_dev_polygon_edge_length = np.std(edge_lengths)
    
    # Total number of polygons
    total_polygons = len(bm.faces)
    
    bm.free()
    polygon_end_time = time.time()
    
    return {
        'avg_texel_size': avg_texel_size,
        'median_texel_size': median_texel_size,
        'std_dev_texel_size': std_dev_texel_size,
        'texture_utilization':texture_utilization,
        'texture_width': texture_width,
        'avg_polygon_edge_length': avg_polygon_edge_length,
        'median_polygon_edge_length': median_polygon_edge_length,
        'std_dev_polygon_edge_length': std_dev_polygon_edge_length,
        'total_polygons': total_polygons
    }

def process_glb_file(glb_file_path):
    if not os.path.isfile(glb_file_path):
        return None

    # Clear existing mesh objects
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.object.select_by_type(type='MESH')
    bpy.ops.object.delete()

    try:
        # Suppress Blender output
        with suppress_output():
            # Import GLB file
            bpy.ops.import_scene.gltf(filepath=glb_file_path)
    except RuntimeError as e:
        print(f"Error importing file {glb_file_path}: {str(e)}")
        return None

    stats = {}
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            stats = calculate_statistics(obj)
            break  # Assuming we only need stats for the first mesh object

    return stats

### Writing csv to disk

def get_unique_tile_id():
    global tile_id_counter
    tile_id_counter += 1
    return tile_id_counter

def load_tileset(tileset_path):
    print(f"Loading tileset: {tileset_path}")
    with open(tileset_path, 'r') as f:
        tileset_data = json.load(f)
    
    root_tile = tileset_data.get('root', {})
    tiles_to_process = []
    process_tile_structure(root_tile, None, 0, tiles_to_process, os.path.dirname(tileset_path))

    # Sort tiles by LOD level
    tiles_to_process.sort(key=lambda x: x['lod_level'])
    
    print(f"Total tiles to process: {len(tiles_to_process)}")
    return tiles_to_process

def process_tile_structure(tile, parent_id, lod_level, tiles_to_process, base_path):
    tile_content = tile.get('content', {})
    tile_uri_template = tile_content.get('uri')
    
    screen_space_error = tile.get('geometricError', None)
    
    if tile_uri_template and '{level}' in tile_uri_template:
        # This is an implicit tiling structure
        pattern = re.compile(r'tiles/(\d+)/(\d+)/(\d+)/(\d+)\.glb')
        
        for root, dirs, files in os.walk(os.path.join(base_path, 'tiles')):
            for file in files:
                if file.endswith('.glb'):
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, base_path)
                    match = pattern.match(relative_path)
                    if match:
                        level, x, y, z = map(int, match.groups())
                        tile_id = get_unique_tile_id()
                        tiles_to_process.append({
                            'lod_level': level,
                            'tile_id': tile_id,
                            'parent_id': parent_id,
                            'tile_url': relative_path,
                            'screen_space_error': screen_space_error,
                            'base_path': base_path,
                            'x': x,
                            'y': y,
                            'z': z
                        })
    else:
        # Handle v1.0 tiles as before
        tile_url = tile_content.get('uri') or tile_content.get('url')
        if tile_url:
            if tile_url.endswith('.json'):
                nested_tileset_path = os.path.join(base_path, tile_url)
                with open(nested_tileset_path, 'r') as f:
                    nested_tileset_data = json.load(f)
                nested_root = nested_tileset_data.get('root', {})
                process_tile_structure(nested_root, parent_id, lod_level, tiles_to_process, os.path.dirname(nested_tileset_path))
            elif tile_url.endswith('.glb'):
                tile_id = get_unique_tile_id()
                tiles_to_process.append({
                    'lod_level': lod_level,
                    'tile_id': tile_id,
                    'parent_id': parent_id,
                    'tile_url': tile_url,
                    'screen_space_error': screen_space_error,
                    'base_path': base_path
                })

    if 'children' in tile:
        for child in tile['children']:
            process_tile_structure(child, tile_id, lod_level + 1, tiles_to_process, base_path)

def write_results_to_csv(results, output_file):
    # Group results by LOD level
    lod_groups = {}
    for result in results:
        lod = result['lod_level']
        if lod not in lod_groups:
            lod_groups[lod] = []
        lod_groups[lod].append(result)

    # Calculate averages for each LOD level, but sum for total_polygons and tile count
    summary = []
    for lod, group in sorted(lod_groups.items()):
        avg_result = {'lod_level': lod, 'tile_id': len(group)}  # tile_id now represents tile count
        for key in group[0].keys():
            if key == 'total_polygons':
                avg_result[key] = sum(r[key] for r in group)
            elif key not in ['lod_level', 'tile_id'] and isinstance(group[0][key], (int, float)):
                avg_result[key] = sum(r[key] for r in group) / len(group)
        summary.append(avg_result)

    # Write to CSV
    keys = results[0].keys()
    with open(output_file, 'w', newline='') as f:
        dict_writer = csv.DictWriter(f, fieldnames=keys)
        
        # Write summary
        dict_writer.writeheader()
        for row in summary:
            rounded_row = {k: f'{v:.4f}' if isinstance(v, float) else v for k, v in row.items()}
            dict_writer.writerow(rounded_row)
        
        # Add empty rows
        for _ in range(3):
            dict_writer.writerow({})
        
        # Write raw data
        dict_writer.writeheader()
        for row in results:
            rounded_row = {k: f'{v:.4f}' if isinstance(v, float) else v for k, v in row.items()}
            dict_writer.writerow(rounded_row)

def process_tile_parallel(tile_info):
    glb_file_path = os.path.join(tile_info['base_path'], tile_info['tile_url'])
    stats = process_glb_file(glb_file_path)
    
    if stats:
        return {
            'lod_level': tile_info['lod_level'],
            'tile_id': tile_info['tile_id'],
            'parent_id': tile_info['parent_id'],
            'tile_uri': tile_info['tile_url'],
            'screen_space_error': tile_info['screen_space_error'],
            **stats
        }
    return None

def main():
    if len(sys.argv) < 6:
        print("Usage: blender --background --python script.py -- <path_to_tileset.json> <output_csv_filename>")
        sys.exit(1)

    tileset_path = sys.argv[-2]
    output_csv = sys.argv[-1]

    tiles_to_process = load_tileset(tileset_path)
    total_tiles = len(tiles_to_process)

    # Create a pool of worker processes
    with multiprocessing.Pool() as pool:
        # Process tiles in parallel with progress indication
        results = []
        for i, result in enumerate(pool.imap(process_tile_parallel, tiles_to_process), 1):
            results.append(result)
            print(f"****  Processed {i}/{total_tiles} tiles. Remaining: {total_tiles - i}")

    # Filter out None results and sort by LOD level
    valid_results = [r for r in results if r is not None]
    valid_results.sort(key=lambda x: x['lod_level'])

    # Write results to CSV
    write_results_to_csv(valid_results, output_csv)
    print(f"Script execution time: {time.time() - script_start_time} seconds")

if __name__ == "__main__":
    main()

   
