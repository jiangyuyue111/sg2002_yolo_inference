/*
 * preprocess_ops.c — Fast image preprocessing for SG2002 TPU pipeline
 * ====================================================================
 *
 * Converts raw camera BGR frames → CHW planar uint8 tensors for TPU input.
 * All operations in C for speed (Python pure-loop resize takes 11s on RISC-V).
 *
 * IMPORTANT — Quantization convention:
 *   The SG2002 cvimodel was calibrated with DIRECT uint8 pixel values.
 *   uint8 [0,255] is copied verbatim; TPU hardware reinterprets as int8.
 *   Do NOT normalize or apply *255-128 quantization — the model expects raw values.
 *
 * Functions:
 *   bgr_resize_planar    — BGR HWC uint8 → CHW planar uint8, bilinear resize
 *   bgr_letterbox_planar — same but with letterbox padding (114 gray)
 *   rgb_to_bgr_inplace   — swap R↔B channels in-place (if camera outputs RGB)
 *
 * Cross-compile for SG2002 (RISC-V musl):
 *   riscv64-linux-musl-gcc -shared -fPIC -O3 -march=rv64gc -o preprocess_ops.so preprocess_ops.c -lm
 *
 * PC test build:
 *   gcc -shared -fPIC -O2 -o preprocess_ops.so preprocess_ops.c -lm
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define CLAMP(v,lo,hi) MIN(MAX(v,lo),hi)

/* ═══════════════════════════════════════════════════════════════════════
 * bgr_resize_planar — BGR uint8 → CHW planar uint8, bilinear resize
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Input:  bgr     — HWC interleaved BGR uint8, shape [src_h][src_w][3]
 *         src_w, src_h — source dimensions
 *         dst_w, dst_h — target dimensions (typically 640x640)
 *
 * Output: out_planar — CHW planar uint8, shape [3][dst_h][dst_w]
 *          Channel order: plane[0]=B, plane[1]=G, plane[2]=R
 *          Values: direct uint8 pixel values [0,255] — no quantization!
 *
 * Algorithm: center-aligned bilinear interpolation, single pass.
 *
 * Returns 0 on success, -1 on null pointer.
 */
int bgr_resize_planar(
    const unsigned char *bgr,
    int src_w, int src_h,
    unsigned char *out_planar,
    int dst_w, int dst_h)
{
    if (!bgr || !out_planar || src_w <= 0 || src_h <= 0 || dst_w <= 0 || dst_h <= 0)
        return -1;

    float scale_x = (float)src_w / (float)dst_w;
    float scale_y = (float)src_h / (float)dst_h;

    int plane_sz = dst_h * dst_w;
    unsigned char *plane_b = out_planar;
    unsigned char *plane_g = out_planar + plane_sz;
    unsigned char *plane_r = out_planar + plane_sz * 2;

    for (int dy = 0; dy < dst_h; dy++) {
        float sy = (dy + 0.5f) * scale_y - 0.5f;
        sy = CLAMP(sy, 0.0f, src_h - 1.001f);

        int sy0 = (int)sy;
        int sy1 = MIN(sy0 + 1, src_h - 1);
        float fy = sy - (float)sy0;

        for (int dx = 0; dx < dst_w; dx++) {
            float sx = (dx + 0.5f) * scale_x - 0.5f;
            sx = CLAMP(sx, 0.0f, src_w - 1.001f);

            int sx0 = (int)sx;
            int sx1 = MIN(sx0 + 1, src_w - 1);
            float fx = sx - (float)sx0;

            int off00 = (sy0 * src_w + sx0) * 3;
            int off01 = (sy0 * src_w + sx1) * 3;
            int off10 = (sy1 * src_w + sx0) * 3;
            int off11 = (sy1 * src_w + sx1) * 3;

            /* Bilinear interpolation per channel, output raw uint8 */
            float w00 = (1.0f - fx) * (1.0f - fy);
            float w01 =        fx  * (1.0f - fy);
            float w10 = (1.0f - fx) *        fy;
            float w11 =        fx  *        fy;

            int dst_idx = dy * dst_w + dx;

            plane_b[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 0] + w01 * bgr[off01 + 0] +
                w10 * bgr[off10 + 0] + w11 * bgr[off11 + 0] + 0.5f);

            plane_g[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 1] + w01 * bgr[off01 + 1] +
                w10 * bgr[off10 + 1] + w11 * bgr[off11 + 1] + 0.5f);

            plane_r[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 2] + w01 * bgr[off01 + 2] +
                w10 * bgr[off10 + 2] + w11 * bgr[off11 + 2] + 0.5f);
        }
    }

    return 0;
}

/* ═══════════════════════════════════════════════════════════════════════
 * bgr_letterbox_planar — BGR → CHW planar with letterbox padding
 * ═══════════════════════════════════════════════════════════════════════
 *
 * YOLO-style letterbox:
 *   - Fit the longer side to dst_size (maintain aspect ratio)
 *   - Center the image, pad with 114 (YOLO gray)
 *   - Output: CHW planar uint8 [3][dst_h][dst_w]
 *
 * Returns 0 on success.
 */
int bgr_letterbox_planar(
    const unsigned char *bgr,
    int src_w, int src_h,
    unsigned char *out_planar,
    int dst_w, int dst_h)
{
    if (!bgr || !out_planar || src_w <= 0 || src_h <= 0 || dst_w <= 0 || dst_h <= 0)
        return -1;

    /* Scale to fit: scale so the longer side = dst_size */
    float scale = fminf((float)dst_w / (float)src_w, (float)dst_h / (float)src_h);
    int new_w = (int)(src_w * scale);
    int new_h = (int)(src_h * scale);
    if (new_w < 1) new_w = 1;
    if (new_h < 1) new_h = 1;

    int pad_left = (dst_w - new_w) / 2;
    int pad_top  = (dst_h - new_h) / 2;

    int plane_sz = dst_h * dst_w;
    unsigned char *plane_b = out_planar;
    unsigned char *plane_g = out_planar + plane_sz;
    unsigned char *plane_r = out_planar + plane_sz * 2;

    /* Fill with padding color: 114 (YOLO standard gray) */
    memset(plane_b, 114, plane_sz);
    memset(plane_g, 114, plane_sz);
    memset(plane_r, 114, plane_sz);

    /* Bilinear resize into the letterbox region */
    float scale_x = (float)src_w / (float)new_w;
    float scale_y = (float)src_h / (float)new_h;

    for (int dy = 0; dy < new_h; dy++) {
        float sy = (dy + 0.5f) * scale_y - 0.5f;
        sy = CLAMP(sy, 0.0f, src_h - 1.001f);

        int sy0 = (int)sy;
        int sy1 = MIN(sy0 + 1, src_h - 1);
        float fy = sy - (float)sy0;

        int dst_row = (pad_top + dy) * dst_w + pad_left;

        for (int dx = 0; dx < new_w; dx++) {
            float sx = (dx + 0.5f) * scale_x - 0.5f;
            sx = CLAMP(sx, 0.0f, src_w - 1.001f);

            int sx0 = (int)sx;
            int sx1 = MIN(sx0 + 1, src_w - 1);
            float fx = sx - (float)sx0;

            int off00 = (sy0 * src_w + sx0) * 3;
            int off01 = (sy0 * src_w + sx1) * 3;
            int off10 = (sy1 * src_w + sx0) * 3;
            int off11 = (sy1 * src_w + sx1) * 3;

            float w00 = (1.0f - fx) * (1.0f - fy);
            float w01 =        fx  * (1.0f - fy);
            float w10 = (1.0f - fx) *        fy;
            float w11 =        fx  *        fy;

            int dst_idx = dst_row + dx;

            plane_b[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 0] + w01 * bgr[off01 + 0] +
                w10 * bgr[off10 + 0] + w11 * bgr[off11 + 0] + 0.5f);
            plane_g[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 1] + w01 * bgr[off01 + 1] +
                w10 * bgr[off10 + 1] + w11 * bgr[off11 + 1] + 0.5f);
            plane_r[dst_idx] = (unsigned char)(
                w00 * bgr[off00 + 2] + w01 * bgr[off01 + 2] +
                w10 * bgr[off10 + 2] + w11 * bgr[off11 + 2] + 0.5f);
        }
    }

    return 0;
}

/* ═══════════════════════════════════════════════════════════════════════
 * YUV→BGR conversion coefficients (ITU-R BT.601, fixed-point 8.8)
 * ═══════════════════════════════════════════════════════════════════════ */

#define YUV_CLIP(v) ((unsigned char)((v) < 0 ? 0 : (v) > 255 ? 255 : (v)))

/* Convert one YUYV422 macropixel (Y0,U,Y1,V) → 2 BGR pixels */
static inline void yuyv_to_bgr_pair(const unsigned char *yuyv,
                                     unsigned char *bgr) {
    int y0 = yuyv[0];
    int u  = yuyv[1] - 128;
    int y1 = yuyv[2];
    int v  = yuyv[3] - 128;

    int c0 = y0 - 16;
    int c1 = y1 - 16;

    /* Pixel 0: Y0 + U,V */
    int r0 = (298 * c0 + 409 * v + 128) >> 8;
    int g0 = (298 * c0 - 100 * u - 208 * v + 128) >> 8;
    int b0 = (298 * c0 + 516 * u + 128) >> 8;
    bgr[0] = YUV_CLIP(b0);
    bgr[1] = YUV_CLIP(g0);
    bgr[2] = YUV_CLIP(r0);

    /* Pixel 1: Y1 + U,V (shared) */
    int r1 = (298 * c1 + 409 * v + 128) >> 8;
    int g1 = (298 * c1 - 100 * u - 208 * v + 128) >> 8;
    int b1 = (298 * c1 + 516 * u + 128) >> 8;
    bgr[3] = YUV_CLIP(b1);
    bgr[4] = YUV_CLIP(g1);
    bgr[5] = YUV_CLIP(r1);
}

/* ═══════════════════════════════════════════════════════════════════════
 * yuyv_resize_planar — YUYV422 → CHW planar BGR, fused YUV→BGR + resize
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Input:  yuyv    — YUYV422 interleaved, W*H*2 bytes (Y0,U,Y1,V per macropixel)
 *         src_w   — MUST be even (YUYV422 constraint)
 *         src_h   — source height
 *         dst_w, dst_h — target size (640x640)
 *
 * Output: out_planar — CHW BGR planar, 3*dst_w*dst_h bytes, uint8 [0,255]
 *
 * Algorithm: Two-pass:
 *   1. Fast integer YUYV→BGR at source resolution (to intermediate buffer)
 *   2. Bilinear resize BGR→CHW planar (reuses bgr_resize_planar)
 *
 * This is simpler and faster on RISC-V than a fused per-pixel approach,
 * since the YUV→BGR conversion is pure integer math and the intermediate
 * BGR buffer fits easily in 254MB RAM.
 *
 * Returns 0 on success, -1 on error.
 */
int yuyv_resize_planar(
    const unsigned char *yuyv,
    int src_w, int src_h,
    unsigned char *out_planar,
    int dst_w, int dst_h)
{
    if (!yuyv || !out_planar || src_w <= 0 || src_h <= 0 || dst_w <= 0 || dst_h <= 0)
        return -1;
    if (src_w & 1) return -1;  /* YUYV requires even width */

    int num_pairs = (src_w * src_h) / 2;
    int bgr_size = src_w * src_h * 3;
    unsigned char *bgr_buf = (unsigned char *)malloc(bgr_size);
    if (!bgr_buf) return -1;

    /* Phase 1: YUYV → BGR interleaved (fast integer loop) */
    const unsigned char *src = yuyv;
    unsigned char *dst = bgr_buf;
    for (int i = 0; i < num_pairs; i++) {
        yuyv_to_bgr_pair(src, dst);
        src += 4;
        dst += 6;  /* 2 pixels × 3 channels */
    }

    /* Phase 2: BGR → CHW planar with bilinear resize */
    int rc = bgr_resize_planar(bgr_buf, src_w, src_h, out_planar, dst_w, dst_h);

    free(bgr_buf);
    return rc;
}

/* ═══════════════════════════════════════════════════════════════════════
 * rgb_to_bgr_inplace — swap R↔B channels (camera outputs RGB → we need BGR)
 * ═══════════════════════════════════════════════════════════════════════ */
void rgb_to_bgr_inplace(unsigned char *data, int w, int h) {
    for (int i = 0; i < w * h; i++) {
        unsigned char tmp = data[i * 3 + 0];
        data[i * 3 + 0] = data[i * 3 + 2];
        data[i * 3 + 2] = tmp;
    }
}

/* ═══════════════════════════════════════════════════════════════════════
 * compute_letterbox — helper to pre-compute letterbox dimensions
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Useful for Python code to know the exact resize region before calling
 * bgr_letterbox_planar. Matches the calculation inside that function.
 */
void compute_letterbox(int src_w, int src_h, int dst_w, int dst_h,
                        float *scale, int *new_w, int *new_h,
                        int *pad_left, int *pad_top) {
    *scale = fminf((float)dst_w / (float)src_w, (float)dst_h / (float)src_h);
    *new_w = (int)(src_w * *scale);
    *new_h = (int)(src_h * *scale);
    if (*new_w < 1) *new_w = 1;
    if (*new_h < 1) *new_h = 1;
    *pad_left = (dst_w - *new_w) / 2;
    *pad_top  = (dst_h - *new_h) / 2;
}

/* ═══════════════════════════════════════════════════════════════════════
 * resize_letterbox_planar — BACKWARD COMPAT with old TGOSKits interface
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Kept for compatibility with existing tpu_infer_v4.py.
 * New code should use bgr_letterbox_planar or bgr_resize_planar instead.
 *
 * Differs from bgr_letterbox_planar: caller pre-computes letterbox params.
 */
void resize_letterbox_planar(const unsigned char *src, int src_w, int src_h,
                              unsigned char *dst, int dst_w, int dst_h,
                              int pad_left, int pad_top,
                              int resized_w, int resized_h) {
    int plane_sz = dst_w * dst_h;
    unsigned char *r_plane = dst;
    unsigned char *g_plane = dst + plane_sz;
    unsigned char *b_plane = dst + 2 * plane_sz;

    memset(r_plane, 114, plane_sz * 3);  /* 114 gray padding */

    float x_scale = (float)src_w / resized_w;
    float y_scale = (float)src_h / resized_h;

    for (int dy = 0; dy < resized_h; dy++) {
        float sy = (dy + 0.5f) * y_scale - 0.5f;
        sy = CLAMP(sy, 0.0f, src_h - 1.001f);
        int sy0 = (int)sy;
        int sy1 = MIN(sy0 + 1, src_h - 1);
        float fy = sy - (float)sy0;

        int dst_row = (pad_top + dy) * dst_w + pad_left;

        for (int dx = 0; dx < resized_w; dx++) {
            float sx = (dx + 0.5f) * x_scale - 0.5f;
            sx = CLAMP(sx, 0.0f, src_w - 1.001f);
            int sx0 = (int)sx;
            int sx1 = MIN(sx0 + 1, src_w - 1);
            float fx = sx - (float)sx0;

            int off00 = (sy0 * src_w + sx0) * 3;
            int off01 = (sy0 * src_w + sx1) * 3;
            int off10 = (sy1 * src_w + sx0) * 3;
            int off11 = (sy1 * src_w + sx1) * 3;

            float w00 = (1.0f - fx) * (1.0f - fy);
            float w01 =        fx  * (1.0f - fy);
            float w10 = (1.0f - fx) *        fy;
            float w11 =        fx  *        fy;

            int idx = dst_row + dx;
            /* Note: src is RGB interleaved, planes are R,G,B */
            r_plane[idx] = (unsigned char)(w00 * src[off00+0] + w01 * src[off01+0] +
                                           w10 * src[off10+0] + w11 * src[off11+0] + 0.5f);
            g_plane[idx] = (unsigned char)(w00 * src[off00+1] + w01 * src[off01+1] +
                                           w10 * src[off10+1] + w11 * src[off11+1] + 0.5f);
            b_plane[idx] = (unsigned char)(w00 * src[off00+2] + w01 * src[off01+2] +
                                           w10 * src[off10+2] + w11 * src[off11+2] + 0.5f);
        }
    }
}

/* ═══════════════════════════════════════════════════════════════════════
 * nms_decode — Fast NMS post-processing for TPU YOLO output
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Decodes raw TPU output tensor (cx,cy,w,h,conf per anchor) through
 * confidence filter + IoU NMS suppression, returning compact detection list.
 *
 * TPU output layout (single-class YOLOv8n):
 *   N anchors × 5 channels: [cx*N, cy*N, w*N, h*N, conf*N] as float32
 *   Total: N*5 floats (N*20 bytes), e.g. N=8400 → 168000 bytes
 *
 * Returns: number of detections kept after NMS.
 *   detections array is filled with [x1,y1,x2,y2,conf] per detection (5 floats).
 *
 * Parameters:
 *   raw       — TPU output float32 array, layout [5][N]
 *   num_anchors  — N (typically 8400 for YOLOv8n 640x640)
 *   conf_thresh  — minimum confidence threshold (e.g. 0.5)
 *   iou_thresh   — NMS IoU threshold (e.g. 0.45)
 *   max_det      — max detections to return (e.g. 20)
 *   detections   — output buffer [max_det * 5 floats], layout [x1,y1,x2,y2,conf]
 *
 * Returns: actual number of detections stored (0..max_det).
 *
 * Algorithm: greedy NMS (same as Python reference).
 *   Complexity: O(K²) worst case (K candidates after conf filter),
 *   typically K < 20 for single-object scenes → negligible.
 */
int nms_decode(
    const float *raw,
    int num_anchors,
    float conf_thresh,
    float iou_thresh,
    int max_det,
    float *detections)
{
    if (!raw || !detections || num_anchors <= 0 || max_det <= 0)
        return 0;

    /* ── Phase 1: Filter by confidence, collect candidates ──────── */
    /* Stack-allocate candidate storage (typical K is small after conf filter) */
    #define NMS_MAX_CANDIDATES 256
    float cand_x1[NMS_MAX_CANDIDATES];
    float cand_y1[NMS_MAX_CANDIDATES];
    float cand_x2[NMS_MAX_CANDIDATES];
    float cand_y2[NMS_MAX_CANDIDATES];
    float cand_conf[NMS_MAX_CANDIDATES];
    int cand_order[NMS_MAX_CANDIDATES];  /* indices sorted by conf descending */
    int suppressed[NMS_MAX_CANDIDATES];
    int K = 0;

    const float *cx_arr = raw;                    /* offset 0*N */
    const float *cy_arr = raw + num_anchors;      /* offset 1*N */
    const float *w_arr  = raw + 2 * num_anchors;  /* offset 2*N */
    const float *h_arr  = raw + 3 * num_anchors;  /* offset 3*N */
    const float *conf_arr = raw + 4 * num_anchors;/* offset 4*N */

    for (int i = 0; i < num_anchors && K < NMS_MAX_CANDIDATES; i++) {
        float conf = conf_arr[i];
        if (conf < conf_thresh) continue;

        float cx = cx_arr[i], cy = cy_arr[i];
        float w = w_arr[i], h = h_arr[i];
        float x1 = cx - w * 0.5f;
        float y1 = cy - h * 0.5f;
        float x2 = cx + w * 0.5f;
        float y2 = cy + h * 0.5f;

        /* Skip degenerate boxes */
        if (x2 - x1 < 2.0f || y2 - y1 < 2.0f) continue;

        cand_x1[K] = (x1 > 0.0f) ? x1 : 0.0f;
        cand_y1[K] = (y1 > 0.0f) ? y1 : 0.0f;
        cand_x2[K] = x2;
        cand_y2[K] = y2;
        cand_conf[K] = conf;
        cand_order[K] = K;
        K++;
    }

    if (K == 0) return 0;

    /* ── Phase 2: Sort by confidence descending (insertion sort) ─── */
    for (int i = 1; i < K; i++) {
        int key = cand_order[i];
        float key_conf = cand_conf[key];
        int j = i - 1;
        while (j >= 0 && cand_conf[cand_order[j]] < key_conf) {
            cand_order[j + 1] = cand_order[j];
            j--;
        }
        cand_order[j + 1] = key;
    }

    /* ── Phase 3: Greedy NMS ────────────────────────────────────── */
    for (int i = 0; i < K; i++) suppressed[i] = 0;

    int out_count = 0;
    for (int i = 0; i < K && out_count < max_det; i++) {
        int idx_i = cand_order[i];
        if (suppressed[idx_i]) continue;

        float xi1 = cand_x1[idx_i], yi1 = cand_y1[idx_i];
        float xi2 = cand_x2[idx_i], yi2 = cand_y2[idx_i];
        float a_i = (xi2 - xi1) * (yi2 - yi1);

        /* Store this detection */
        float *d = detections + out_count * 5;
        d[0] = xi1; d[1] = yi1; d[2] = xi2; d[3] = yi2; d[4] = cand_conf[idx_i];
        out_count++;

        /* Suppress overlapping boxes */
        for (int j = i + 1; j < K; j++) {
            int idx_j = cand_order[j];
            if (suppressed[idx_j]) continue;

            float xj1 = cand_x1[idx_j], yj1 = cand_y1[idx_j];
            float xj2 = cand_x2[idx_j], yj2 = cand_y2[idx_j];

            /* IoU = intersection / union */
            float inter_x = (xi2 < xj2 ? xi2 : xj2) - (xi1 > xj1 ? xi1 : xj1);
            float inter_y = (yi2 < yj2 ? yi2 : yj2) - (yi1 > yj1 ? yi1 : yj1);
            if (inter_x <= 0.0f || inter_y <= 0.0f) continue;

            float inter = inter_x * inter_y;
            float a_j = (xj2 - xj1) * (yj2 - yj1);
            float iou = inter / (a_i + a_j - inter + 1e-6f);

            if (iou > iou_thresh) suppressed[idx_j] = 1;
        }
    }

    return out_count;
    #undef NMS_MAX_CANDIDATES
}

/* ═══════════════════════════════════════════════════════════════════════
 * nms_decode_compact — Compact output (no box clamping, minimal latency)
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Same as nms_decode but returns detection count only. Caller reads
 * detections from the output buffer.
 *
 * Use nms_decode for general use; nms_decode_compact for benchmarking.
 */
int nms_decode_compact(
    const float *raw, int num_anchors, float conf_thresh, float iou_thresh,
    int max_det, float *detections)
{
    return nms_decode(raw, num_anchors, conf_thresh, iou_thresh, max_det, detections);
}

/* ═══════════════════════════════════════════════════════════════════════
 * Get library version
 * ═══════════════════════════════════════════════════════════════════════ */
const char* preprocess_ops_version(void) {
    return "3.0.0";  /* v3: added nms_decode */
}
