"""顔認識および検出機能。"""

from .face_function import save_face_image, detect_persons_in_frame, detect_faces_in_frame, draw_boxes_on_frame

__all__ = ["save_face_image", "detect_persons_in_frame", "detect_faces_in_frame"]
