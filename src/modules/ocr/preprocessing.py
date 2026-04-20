from __future__ import annotations

import base64
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PreprocessResult:
    original_bgr: np.ndarray
    enhanced_gray: np.ndarray
    deskewed_gray: np.ndarray
    cropped_gray: np.ndarray
    ocr_bgr: np.ndarray
    tiles_bgr: list[np.ndarray]
    crop_box: tuple[int, int, int, int]


def enhance_gray(img_bgr: np.ndarray, target_long: int = 2600) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    long_side = max(height, width)
    if target_long and long_side > target_long:
        scale = target_long / float(long_side)
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_CUBIC)

    bg = cv2.medianBlur(gray, 31)
    norm = cv2.divide(gray, bg, scale=255)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    norm = clahe.apply(norm)
    blur = cv2.GaussianBlur(norm, (0, 0), 1.1)
    return cv2.addWeighted(norm, 1.35, blur, -0.35, 0)


def deskew(gray: np.ndarray) -> np.ndarray:
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = 255 - bw
    coords = np.column_stack(np.where(inv > 0))
    if coords.size < 2000:
        return gray

    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.7:
        return gray

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width // 2, height // 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def crop_foreground(gray: np.ndarray, margin: int = 60) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    inv = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        12,
    )
    coords = np.column_stack(np.where(inv > 0))
    if coords.size == 0:
        height, width = gray.shape
        return gray, (0, 0, width, height)

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0)
    height, width = gray.shape
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(width - 1, x1 + margin)
    y1 = min(height - 1, y1 + margin)
    return gray[y0 : y1 + 1, x0 : x1 + 1], (int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1))


def resize_long_side(img: np.ndarray, target_long: int, upscale_limit: float = 1.0) -> np.ndarray:
    height, width = img.shape[:2]
    long_side = max(height, width)
    if long_side == 0:
        return img
    scale = target_long / float(long_side)
    if scale > 1.0:
        scale = min(scale, upscale_limit)
    if abs(scale - 1.0) < 1e-3:
        return img
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    interpolation = cv2.INTER_CUBIC if scale >= 1.0 else cv2.INTER_AREA
    return cv2.resize(img, (new_width, new_height), interpolation=interpolation)


def split_vertical_with_overlap(img: np.ndarray, parts: int = 2, overlap_px: int = 200) -> list[np.ndarray]:
    height, _ = img.shape[:2]
    if parts <= 1 or height < 900:
        return [img]
    step = height // parts
    tiles = []
    for index in range(parts):
        y0 = max(0, index * step - (overlap_px if index > 0 else 0))
        y1 = min(height, (index + 1) * step + (overlap_px if index < parts - 1 else 0))
        tiles.append(img[y0:y1, :])
    return tiles


def preprocess_page(
    img_bgr: np.ndarray,
    *,
    preprocess_long: int = 2600,
    min_long_for_ocr: int = 1800,
    tile: bool = True,
) -> PreprocessResult:
    enhanced = enhance_gray(img_bgr, target_long=preprocess_long)
    deskewed = deskew(enhanced)
    cropped, crop_box = crop_foreground(deskewed)
    ocr_ready = resize_long_side(cropped, target_long=min_long_for_ocr, upscale_limit=1.0)
    ocr_bgr = cv2.cvtColor(ocr_ready, cv2.COLOR_GRAY2BGR)
    tiles = split_vertical_with_overlap(ocr_bgr, parts=2, overlap_px=200) if tile else [ocr_bgr]
    return PreprocessResult(
        original_bgr=img_bgr,
        enhanced_gray=enhanced,
        deskewed_gray=deskewed,
        cropped_gray=cropped,
        ocr_bgr=ocr_bgr,
        tiles_bgr=tiles,
        crop_box=crop_box,
    )


def png_bytes(img: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("failed to encode image as PNG")
    return buffer.tobytes()


def b64_png(img: np.ndarray) -> str:
    return base64.b64encode(png_bytes(img)).decode("ascii")
