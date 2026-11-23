"""
Constants and knowledge base for Whisper Hallucination Filtering.

Contains:
- STRONG_KEYWORDS: Fast-exit keywords (Layer 1) to catch suspicious phrases.
- RAW_HALLUCINATIONS: Bag-of-Hallucinations (BoH) seed dataset for Fuzzy & Semantic matching.
"""

from typing import List

# ====================== LAYER 1: STRONG KEYWORDS (FAST EXIT) ======================
# Fast check (< 0.1ms). If a transcript segment does NOT contain any of these keywords,
# the filter exits immediately without executing more expensive layers.
#
# NOTE: Avoid single common words like "video" alone to prevent false positives in
# normal meeting discussions (e.g. "turn on video", "send video").
STRONG_KEYWORDS: List[str] = [
    "đăng ký kênh",
    "đăng kí kênh",
    "ủng hộ kênh",
    "ủng hộ mình",
    "ủng hộ cho mình",
    "cảm ơn các bạn đã theo dõi",
    "cảm ơn đã theo dõi",
    "cảm ơn quý vị đã theo dõi",
    "cảm ơn mọi người đã theo dõi",
    "share video",
    "chia sẻ video",
    "nhận thêm những video",
    "video mới nhất",
    "video hấp dẫn",
    "lalaschool",
    "lala school",
    "hãy đăng ký",
    "nhớ đăng ký",
    "đăng ký nhé",
    "đăng ký ngay",
    "ủng hộ kênh của mình",
    "ủng hộ kênh của chúng mình",
    "hẹn gặp lại các bạn",
    "hẹn gặp lại quý vị",
    "nhấn chuông thông báo",
    "like và subscribe",
    "chúc các bạn xem video",
    "xem video vui vẻ",
    "ghien mi go",
    "ghiền mì gõ",
    "không bỏ lỡ những video",
    "theo dõi và hẹn gặp lại",
    "subscribe cho kênh",
    "giây phút thư giãn",
    "đón xem chương trình",
    "quan tâm theo dõi",
]

# ====================== LAYERS 2 & 3: BAG OF HALLUCINATIONS (BoH) ======================
# Used for fuzzy matching (Rapidfuzz) and semantic embedding similarity (ONNX).
RAW_HALLUCINATIONS: List[str] = [
    "Cảm ơn các bạn đã theo dõi",
    "Các bạn hãy đăng ký kênh để ủng hộ kênh của mình nhé",
    "Các bạn hãy đăng kí cho kênh lalaschool",
    "hãy đăng ký kênh để ủng hộ kênh của mình nhé",
    "Các bạn nhớ đăng ký kênh để ủng hộ cho mình",
    "và share video này để ủng hộ kênh của mình",
    "và đăng ký kênh để nhận thêm những video mới nhất",
    "Các bạn có thể nhận thêm những video hấp dẫn",
    "lalaschool",
    "Cảm ơn các bạn đã theo dõi và lalaschool",
    "Cảm ơn quý vị và các bạn đã quan tâm theo dõi",
    "Hẹn gặp lại quý vị và các bạn trong những video tiếp theo",
    "Đừng quên nhấn like và đăng ký kênh để ủng hộ mình nhé",
    "Chúc các bạn có những giây phút thư giãn vui vẻ",
    "Hãy chia sẻ video để ủng hộ kênh nhé",
    "Cảm ơn mọi người đã theo dõi video",
    "Nhớ đăng ký kênh và nhấn chuông thông báo nhé",
    "Hãy subscribe cho kênh ghiền mì gõ để không bỏ lỡ những video hấp dẫn",
    "để không bỏ lỡ những video hấp dẫn",
    "Cảm ơn các bạn đã theo dõi và hẹn gặp lại",
    "Hãy subscribe cho kênh lala school",
    "Hãy subscribe cho kênh",
    "Cảm ơn quý vị khán giả đã chú ý đón xem chương trình",
]
