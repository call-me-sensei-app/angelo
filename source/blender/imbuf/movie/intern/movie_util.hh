/* SPDX-FileCopyrightText: 2024 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup imbuf
 */

#ifdef WITH_FFMPEG

extern "C" {
#  include <libavcodec/avcodec.h>
#  include <libavutil/hwcontext.h>
#  include <libavutil/pixfmt.h>
}
#  include "DNA_scene_types.h"

struct AVFrame;
struct AVStream;
struct AVFormatContext;

namespace blender {

int ffmpeg_deinterlace(
    AVFrame *dst, const AVFrame *src, enum AVPixelFormat pix_fmt, int width, int height);

/**
 * Whether `stream` should be treated as a loadable video stream. Only count attached pictures
 * (cover art / embedded thumbnails) if there are no other "real" video streams in the container as
 * a fallback.
 */
bool ffmpeg_stream_counts_as_video(const AVFormatContext *format_ctx, const AVStream *stream);

/**
 * Angelo: open a file for FFmpeg to read or write through the `file` protocol only. Blender's bundled FFmpeg is
 * built with network protocols (http, tcp, udp, rtp, rtmp, ftp), and a path is whatever a document or a script set:
 * a URL there made a sound, a movie or an encoder connect out, as native traffic no audit event sees (BREAKIT
 * reread-r210-1c2c4edaf48 M-1). FFmpeg hands the whitelist on to everything a format opens inside the file (a
 * playlist's segments, a concat list's parts), so those stay local too. Same return value as `avformat_open_input`.
 * The two writers (movie_write.cc, movie_proxy_indexer.cc) pass the same list to `avio_open2` at their own gated
 * sites (c_write_sinks).
 */
int ffmpeg_open_input_local(AVFormatContext **format_ctx, const char *filepath);

const char *ffmpeg_last_error();
AVCodecID mov_av_codec_id_get(IMB_Ffmpeg_Codec_ID id);
AVBufferRef *ffmpeg_hw_device_get(AVHWDeviceType device_type);

/** Checks whether given FFMPEG codec and profile combination supports alpha channel (RGBA). */
bool MOV_codec_supports_alpha(AVCodecID codec_id, int ffmpeg_profile);

/**
 * Checks whether given FFMPEG video AVCodecID supports CRF (i.e. "quality level")
 * setting. For codecs that do not support constant quality, only target bit-rate
 * can be specified.
 */
bool MOV_codec_supports_crf(AVCodecID codec_id);

/**
 * Which pixel bit depths are supported by a given FFMPEG video CodecID.
 * Returns bit-mask of `R_IMF_CHAN_DEPTH_` flags.
 */
eImageFormatDepth MOV_codec_valid_bit_depths(AVCodecID codec_id);

/**
 * Returns thread count to be used for ffmpeg.
 */
int MOV_thread_count();

}  // namespace blender

#endif /* WITH_FFMPEG */
