# SPDX-FileCopyrightText: 2018-2021 The glTF-Blender-IO authors
#
# SPDX-License-Identifier: Apache-2.0

from ctypes import *

from ...io.com.gltf2_io import BufferView
from ...io.imp.gltf2_io_binary import BinaryData
from ...io.com.library import dll_path
from ...io.imp.gltf2_io_gltf import ImportError  # Angelo: the importer's own class, which its operator reports.


def decode_primitive(gltf, prim):
    """
    Handles draco compression.
    Moves decoded data into new buffers and buffer views held by the accessors of the given primitive.
    """

    # Load DLL and setup function signatures.
    dll = cdll.LoadLibrary(str(dll_path('bf_intern_draco_bridge', 'Draco').resolve()))

    dll.decoderCreate.restype = c_void_p
    dll.decoderCreate.argtypes = []

    dll.decoderRelease.restype = None
    dll.decoderRelease.argtypes = [c_void_p]

    dll.decoderDecode.restype = c_bool
    dll.decoderDecode.argtypes = [c_void_p, c_void_p, c_size_t]

    dll.decoderReadAttribute.restype = c_bool
    dll.decoderReadAttribute.argtypes = [c_void_p, c_uint32, c_size_t, c_char_p]

    dll.decoderGetVertexCount.restype = c_uint32
    dll.decoderGetVertexCount.argtypes = [c_void_p]

    dll.decoderGetIndexCount.restype = c_uint32
    dll.decoderGetIndexCount.argtypes = [c_void_p]

    dll.decoderAttributeIsNormalized.restype = c_bool
    dll.decoderAttributeIsNormalized.argtypes = [c_void_p, c_uint32]

    dll.decoderGetAttributeByteLength.restype = c_size_t
    dll.decoderGetAttributeByteLength.argtypes = [c_void_p, c_uint32]

    dll.decoderCopyAttribute.restype = None
    dll.decoderCopyAttribute.argtypes = [c_void_p, c_uint32, c_void_p]

    dll.decoderReadIndices.restype = c_bool
    dll.decoderReadIndices.argtypes = [c_void_p, c_size_t]

    dll.decoderGetIndicesByteLength.restype = c_size_t
    dll.decoderGetIndicesByteLength.argtypes = [c_void_p]

    dll.decoderCopyIndices.restype = None
    dll.decoderCopyIndices.argtypes = [c_void_p, c_void_p]

    decoder = dll.decoderCreate()
    extension = prim.extensions['KHR_draco_mesh_compression']

    name = prim.name if hasattr(prim, 'name') else '[unnamed]'

    # Create Draco decoder.
    draco_buffer = bytes(BinaryData.get_buffer_view(gltf, extension['bufferView']))
    # Angelo: bound what the native decoder will build before it runs (see check_header).
    index_count = gltf.data.accessors[prim.indices].count if prim.indices is not None else 0
    position = prim.attributes.get('POSITION') if isinstance(prim.attributes, dict) else None
    vertex_count = gltf.data.accessors[position].count if position is not None else 0
    try:
        point_limit = check_header(draco_buffer, index_count, vertex_count)
    except ImportError:
        dll.decoderRelease(decoder)
        raise
    if not dll.decoderDecode(decoder, draco_buffer, len(draco_buffer)):
        gltf.log.error('Draco Decoder: Unable to decode. Skipping primitive {}.'.format(name))
        return

    # Choose a buffer index which does not yet exist, skipping over existing glTF buffers yet to be loaded
    # and buffers which were generated and did not exist in the initial glTF file, like this decoder does.
    base_buffer_idx = len(gltf.data.buffers)
    for existing_buffer_idx in gltf.buffers:
        if base_buffer_idx <= existing_buffer_idx:
            base_buffer_idx = existing_buffer_idx + 1

    # Read indices.
    index_accessor = gltf.data.accessors[prim.indices]
    if dll.decoderGetIndexCount(decoder) > index_accessor.count:
        # Angelo: never decode more than the accessor declares (callers bound untrusted files by accessor counts).
        dll.decoderRelease(decoder)
        raise ImportError('Draco Decoder: the stream holds more indices than its accessor declares.')
    if dll.decoderGetIndexCount(decoder) != index_accessor.count:
        gltf.log.warning(
            'Draco Decoder: Index count of accessor and decoded index count does not match. Updating accessor.')
        index_accessor.count = dll.decoderGetIndexCount(decoder)
    if not dll.decoderReadIndices(decoder, index_accessor.component_type):
        gltf.log.error('Draco Decoder: Unable to decode indices. Skipping primitive {}.'.format(name))
        return

    indices_byte_length = dll.decoderGetIndicesByteLength(decoder)
    decoded_data = bytes(indices_byte_length)
    dll.decoderCopyIndices(decoder, decoded_data)

    # Generate a new buffer holding the decoded indices.
    gltf.buffers[base_buffer_idx] = decoded_data

    # Create a buffer view referencing the new buffer.
    gltf.data.buffer_views.append(BufferView.from_dict({
        'buffer': base_buffer_idx,
        'byteLength': indices_byte_length
    }))

    # Update accessor to point to the new buffer view.
    index_accessor.buffer_view = len(gltf.data.buffer_views) - 1

    # Read each attribute.
    for attr_idx, attr in enumerate(extension['attributes']):
        dracoId = extension['attributes'][attr]
        if attr not in prim.attributes:
            gltf.log.error(
                'Draco Decoder: Draco attribute {} not in primitive attributes. Skipping primitive {}.'.format(
                    attr, name))
            return

        accessor = gltf.data.accessors[prim.attributes[attr]]
        if dll.decoderGetVertexCount(decoder) > point_limit:
            dll.decoderRelease(decoder)   # Angelo: as for indices above; split non-manifold vertices may add points.
            raise ImportError('Draco Decoder: the stream holds more vertices than its accessors allow.')
        if dll.decoderGetVertexCount(decoder) != accessor.count:
            gltf.log.warning(
                'Draco Decoder: Vertex count of accessor and decoded vertex count does not match for attribute {}. Updating accessor.'.format(
                    attr,
                    name))
            accessor.count = dll.decoderGetVertexCount(decoder)
        if not dll.decoderReadAttribute(decoder, dracoId, accessor.component_type, accessor.type.encode()):
            gltf.log.error('Draco Decoder: Could not decode attribute {}. Skipping primitive {}.'.format(attr, name))
            return

        byte_length = dll.decoderGetAttributeByteLength(decoder, dracoId)
        decoded_data = bytes(byte_length)
        dll.decoderCopyAttribute(decoder, dracoId, decoded_data)

        # Generate a new buffer holding the decoded vertex data.
        buffer_idx = base_buffer_idx + 1 + attr_idx
        gltf.buffers[buffer_idx] = decoded_data

        # Create a buffer view referencing the new buffer.
        gltf.data.buffer_views.append(BufferView.from_dict({
            'buffer': buffer_idx,
            'byteLength': byte_length
        }))

        # Update accessor to point to the new buffer view.
        accessor.buffer_view = len(gltf.data.buffer_views) - 1

    dll.decoderRelease(decoder)


# Angelo: the native decoder builds the whole mesh before any count can be compared, and a small stream can declare
# millions of faces. Read the face count from the stream's header (KHR_draco_mesh_compression requires Draco bitstream
# 2.2) and refuse a stream with more faces than its index accessor declares (and MAX_FACES), or more vertices than
# its POSITION accessor or three per face, whichever is larger: Edgebreaker splits non-manifold vertices, so a valid
# stream may hold more points than POSITION, never more than three per face. A header this can't read is refused.
# Not bounded here: the number and width of attributes the stream carries (all are decoded).
MAX_FACES = 16_000_000
SEQUENTIAL, EDGEBREAKER = 0, 1


def _varint(data, at):
    value = shift = 0
    while True:
        if at >= len(data) or shift > 28:
            raise ImportError("Draco Decoder: malformed header")
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7


def header_counts(data):
    """``(faces, vertices)`` a Draco 2.2 triangle-mesh stream declares (sequential: its points; Edgebreaker: its
    encoded vertices)."""
    if len(data) < 11 or data[:5] != b"DRACO" or (data[5], data[6]) != (2, 2):
        raise ImportError("Draco Decoder: only Draco 2.2 streams are imported")
    encoder_type, method, flags = data[7], data[8], int.from_bytes(data[9:11], "little")
    if encoder_type != 1 or flags & 0x8000:
        raise ImportError("Draco Decoder: only triangle meshes without metadata are imported")
    if method == SEQUENTIAL:
        faces, at = _varint(data, 11)
        vertices, _at = _varint(data, at)
    elif method == EDGEBREAKER:
        vertices, at = _varint(data, 12)   # After the traversal type byte.
        faces, _at = _varint(data, at)
    else:
        raise ImportError("Draco Decoder: unknown connectivity encoding")
    return faces, vertices


def check_header(data, index_count, vertex_count):
    """Refuse a stream declaring more geometry than its accessors; returns the most points it may decode to."""
    faces, vertices = header_counts(data)
    limit = max(vertex_count, faces * 3)
    if faces > MAX_FACES or faces * 3 > index_count or vertices > limit:
        raise ImportError("Draco Decoder: the stream declares more geometry than its accessors")
    return limit

