# 3d Tile Statistics Generator

This script parses all the mesh files in an OGC 3d tileset, and generates some statistics about the mesh and texture.
It works on 3D Tiles v1.0 as well as v1.1 implicit tiles.

The extracted information:
- Hirearchy of the LOD files.
- Screen Space Error : extracted from the tileset.json file. With v1.1 implicit tiles, the value is the root level SSE. Each LOD should be 1/2 the value of the previous LOD.
- Texel size: Average, Mean, Standard Deviation data is computed for each texture of each tile.
- Texture Utilization: What percentage of the texture is used up by UV space that is mapped onto polygons.
- Polygon Edge: Average, Mean, Standard Deviation data is computed for each mesh in each tile.
- Total Polygons: Polygon count for each tile.

The data is sorted by LOD level.

The top portion of the csv file is a summary which summarizes the data for each LOD.
Below the summary is the data for each tile.

In the summary, the `tile_id` column is actually the number of tiles in that LOD level. (need to fix the header title)

### Before usage
When dealing with v1.0 3d tiles, you will need to convert the b3dm files in the tileset to glb, and you'll have to replace all refereences of b3dm in the tileset.json files (and any other nested json files) to glb.
The script currently only works with glb files.


### Usage

`blender --background --python examine_3d-tile.py -- <path_to_tileset.json> <output_csv_filename>`

Blender needs to be installed as the pythoin script uses it for the heavy lifting of texture and mesh computation.
