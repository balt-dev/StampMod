import os
import sys
import random
import threading
import tempfile
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import cv2 
# Image processing libraries
from PIL import Image, ImageSequence, ImageGrab, ImageQt, ImageFilter, UnidentifiedImageError
import numpy as np
import logging
import shutil
# Scikit-learn and SciPy utilities
from sklearn.cluster import KMeans
from joblib import parallel_backend
# PySide6 (Qt framework)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QCheckBox, QSlider, QComboBox,
    QProgressBar, QMessageBox, QStackedWidget, QLineEdit, QSizePolicy,
    QFormLayout, QGridLayout, QSpacerItem, QFrame, QStackedLayout, QScrollArea
)
from PySide6.QtGui import (
    QPixmap, QMovie, QIcon, QPainter, QCursor, QImage
)
from PySide6.QtCore import (
    Qt, Signal, QObject, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSize, QThread, Slot
)

def get_base_path() -> Path:
    if getattr(sys, 'frozen', False):
        # If the application is frozen, use the executable's directory
        base_path = Path(sys.executable).parent
    else:
        # If not frozen, use the script's directory
        base_path = Path(__file__).parent

    # Trim just the current script directory
    return base_path.parent

def exe_path_fs(relative_path: str) -> Path:
    base_path = get_base_path()
    return (base_path / relative_path).resolve()

def exe_path_stylesheet(relative_path: str) -> str:
    # For Qt stylesheets, we need forward slashes
    return exe_path_fs(relative_path).as_posix()

def get_appdata_dir() -> Path:
    """
    Get the system-specific AppData/Local directory for storing application data.

    Returns:
        Path: The path to the application-specific directory in AppData/Local.
    """
    if os.name == "nt":  # Windows
        appdata_base = Path(os.getenv("LOCALAPPDATA"))
    else:  # Linux/macOS
        appdata_base = Path.home() / ".local" / "share"
    
    appdata_dir = appdata_base / "webfishing_stamps_mod"
    appdata_dir.mkdir(parents=True, exist_ok=True)  # Ensure it exists
    return appdata_dir

def get_config_path() -> Path:
    """
    Get the path to the configuration file for PurplePuppy Stamps.

    Returns:
        Path: The full path to the configuration file.
    """
    # Start with the base path of the executable or script
    base_path = get_base_path()

    # Navigate up until we reach 'GDWeave'
    while base_path.name in ["mods", "PurplePuppy-Stamps"]:
        base_path = base_path.parent

    # Ensure the resolved base path is correct
    if base_path.name != "GDWeave":
        raise ValueError(f"Base path resolution error: {base_path} is not GDWeave.")

    # Navigate to the sibling 'configs' directory
    config_dir = (base_path / "configs").resolve()

    # Ensure the configs directory exists
    config_dir.mkdir(parents=True, exist_ok=True)

    # Define the specific configuration file name
    config_file = config_dir / "PurplePuppy.Stamps.json"

    return config_file




# Create a registry dictionary
processing_method_registry = {}
use_lab = False
first = False
auto_color_boost = True


exe_directory = exe_path_fs('exe_data')


if not exe_directory.exists():
    exe_directory.mkdir(parents=True, exist_ok=True)



def register_processing_method(name, default_params=None, description=""):
    """
    Decorator to register a processing method.

    Parameters:
    - name (str): Name of the processing method.
    - default_params (dict): Default parameters for the processing method.
    - description (str): A brief description of the processing method.
    """
    def decorator(func):
        func.default_params = default_params or {}
        func.description = description
        processing_method_registry[name] = func
        return func
    return decorator

def prepare_image(img):
    """
    Converts image to RGBA if not already, and returns a writable copy of the image.
    """
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    # Make a writable copy of the image
    img = img.copy()
    return img

def remove_background(input_image, message_callback=None, model_path=None, threshold=None):
    """
    Process an image using the U2NET ONNX model with improved preprocessing steps.

    Steps:
    - Make a copy of the original image.
    - Convert to RGB if not already.
    - Apply LAB-based CLAHE for local contrast enhancement.
    - Apply mild unsharp masking to highlight edges.
    - Resize the enhanced copy to 512x512 (model input size).
    - Run model inference to get a mask.
    - Resize mask back to original size.
    - Apply mask to original image and return.
    """
    import onnxruntime as ort
    if message_callback is None:
        message_callback = print

    try:
        # Load model path
        model_path = exe_path_fs("exe_data/remove_bg.onnx")
        message_callback(f"Using ONNX model at: {model_path}")

        if not isinstance(input_image, Image.Image):
            raise ValueError("Input must be a PIL.Image.Image instance.")

        # Original size
        original_width, original_height = input_image.size
        message_callback(f"Original image mode: {input_image.mode}, size: {input_image.size}")

        # Make a copy for preprocessing
        work_image = input_image.copy()

        # Ensure RGB
        if work_image.mode != "RGB":
            work_image = work_image.convert("RGB")
            message_callback("Converted input image to RGB for processing.")

        # ==== Preprocessing Step: LAB CLAHE ====
        # Convert to LAB
        work_np = np.array(work_image)
        lab_image = cv2.cvtColor(work_np, cv2.COLOR_RGB2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab_image)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        l_channel_clahe = clahe.apply(l_channel)

        lab_clahe = cv2.merge((l_channel_clahe, a_channel, b_channel))
        enhanced_rgb = cv2.cvtColor(lab_clahe, cv2.COLOR_LAB2RGB)
        work_image = Image.fromarray(enhanced_rgb)

        # ==== Preprocessing Step: Mild Unsharp Masking ====
        work_image = work_image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))
        message_callback("Applied LAB-based CLAHE and unsharp masking for better subject clarity.")

        # Resize to 512x512 for model input
        target_size = (512, 512)
        resized_image = work_image.resize(target_size, Image.LANCZOS)
        message_callback(f"Resized work image to {target_size} for model inference.")

        # Convert to normalized numpy array
        img_array = np.array(resized_image).astype(np.float32) / 255.0
        img_array = np.transpose(img_array, (2, 0, 1))  # [C, H, W]
        img_array = np.expand_dims(img_array, axis=0)    # [1, C, H, W]

        message_callback(f"Input array shape for ONNX model: {img_array.shape}")

        # Run ONNX inference
        session = ort.InferenceSession(model_path)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: img_array})
        output = outputs[0][0, 0]  # [H, W]

        message_callback(f"Model output shape: {output.shape}, max value: {output.max():.3f}")

        # Dynamic threshold if not provided
        def calculate_dynamic_threshold(mask):
            mean_value = np.mean(mask)
            max_value = np.max(mask)
            calculated_threshold = max(0.01, min(mean_value * 0.5, max_value * 0.5))
            adjusted_threshold = max(0.01, calculated_threshold * 0.9 - 0.01) / 42
            return adjusted_threshold

        if threshold is None:
            threshold = calculate_dynamic_threshold(output)
            print(threshold)
            message_callback(f"Dynamically adjusted threshold: {threshold:.3f}")

        # Create alpha mask
        alpha_mask = (output >= threshold).astype(np.uint8) * 255
        alpha_mask_img = Image.fromarray(alpha_mask, mode='L')

        # Resize the mask back to the original size
        alpha_mask_resized = alpha_mask_img.resize((original_width, original_height), Image.LANCZOS)
        message_callback("Resized alpha mask back to original image dimensions.")

        # Apply the mask to the original image
        original_rgba = input_image.convert("RGBA")
        final_image = Image.new("RGBA", (original_width, original_height), (255, 255, 255, 0))
        final_image.paste(original_rgba, mask=alpha_mask_resized)
        message_callback("Applied the mask to the original image.")

        return final_image

    except Exception as e:
        message_callback(f"An error occurred: {e}")
        return input_image
    
def compute_brightness_adjustment(lab_image, use_lab, default_adjustment=0.5):
    """
    Helper function to compute a suitable brightness adjustment factor based on the input LAB image
    and whether LAB color space is being used for color boosting.

    Parameters:
    - lab_image (np.ndarray): Input image in LAB color space with shape (H, W, 3).
    - use_lab (bool): Flag indicating if LAB color space is used for color boosting.
    - default_adjustment (float): Default brightness adjustment value to use if computation fails.

    Returns:
    - float: Brightness adjustment factor between 0 and 1.
    """
    try:
        # Extract the L channel
        L = lab_image[:, :, 0].astype(np.float32)
        
        # Compute the mean brightness
        mean_brightness = np.mean(L)
        
        # Normalize mean brightness to range [0, 1]
        normalized_brightness = mean_brightness / 255.0
        
        # Map normalized brightness to brightness adjustment factor
        # Assuming that mean_brightness around 128 corresponds to no adjustment (0.5)
        brightness_adjustment = normalized_brightness / 2.0
        
        # Adjust mapping based on use_lab flag
        if use_lab:
            # In LAB mode, prioritize balanced brightness
            brightness_adjustment = 0.5  # No adjustment by default
        else:
            # In RGB mode, allow dynamic adjustment based on brightness
            brightness_adjustment = np.clip(brightness_adjustment, 0.0, 1.0)
        print(brightness_adjustment)
        return brightness_adjustment
    except Exception as e:
        print(f"Error computing brightness adjustment: {e}")
        return default_adjustment

def adjust_boost_threshold(ckey, use_lab, lab_image, hsv_image, config):
    """
    Helper function to adjust the boost and threshold values for a given color key based on
    whether LAB or HSV color space is being used.

    Parameters:
    - ckey (dict): A dictionary containing 'number', 'hex', 'boost', and 'threshold' for a color.
    - use_lab (bool): Flag indicating if LAB color space is used for color boosting.
    - lab_image (np.ndarray): Input image in LAB color space with shape (H, W, 3).
    - hsv_image (np.ndarray): Input image in HSV color space with shape (H, W, 3).
    - config (dict): Configuration dictionary containing processing parameters.

    Returns:
    - tuple: Updated (boost, threshold) values.
    """
    hex_code = ckey['hex'].lstrip('#')
    color_rgb = tuple(int(hex_code[i:i + 2], 16) for i in (0, 2, 4))
    boost = ckey.get('boost', 1.2)
    threshold = ckey.get('threshold', config['threshold_default'])

    if use_lab:
        # Convert color to LAB
        color_lab = cv2.cvtColor(np.uint8([[color_rgb]]), cv2.COLOR_RGB2LAB)[0, 0]
        # Calculate Euclidean distance in LAB space
        distance = np.linalg.norm(lab_image.astype(np.float32) - color_lab.astype(np.float32), axis=2)
        # Define a proximity threshold (you may need to adjust this)
        proximity_thresh = threshold
        color_mask = (distance < proximity_thresh)
    else:
        # Convert color to HSV
        color_hsv = cv2.cvtColor(np.uint8([[color_rgb]]), cv2.COLOR_RGB2HSV)[0, 0]
        target_h = color_hsv[0]
        hue_diff = np.abs(hsv_image[:, :, 0].astype(np.float32) - target_h)
        hue_diff = np.minimum(hue_diff, 180 - hue_diff)
        # Dynamic threshold: 20–35
        dynamic_thresh = max(20, min(35, np.std(hue_diff[hue_diff < threshold]) * 1.5))
        # Dynamic boost: if mean_sat < 80 higher boost, if >150 lower boost
        sat_vals = hsv_image[:, :, 1][hue_diff < threshold]
        mean_sat = np.mean(sat_vals) if len(sat_vals) > 0 else 128.0

        if mean_sat < 80:
            dynamic_boost = 1.4
        elif mean_sat > 150:
            dynamic_boost = 1.1
        else:
            dynamic_boost = 1.2

        # Apply negative boost logic if specified
        if 'boost' in ckey and isinstance(ckey['boost'], (int, float)) and ckey['boost'] < 0:
            neg_val = abs(ckey['boost'])
            boost = max(0.5, 1.0 - 0.1 * neg_val)
        else:
            boost = dynamic_boost

        # Apply threshold logic
        if 'threshold' in ckey and isinstance(ckey['threshold'], (int, float)) and ckey['threshold'] < 0:
            threshold = dynamic_thresh
        else:
            threshold = threshold

        # Recalculate color_mask with updated threshold
        color_mask = (hue_diff < threshold)
        ckey['boost'] = boost
        ckey['threshold'] = threshold

    # Apply boost to saturation where mask is True
    return boost, threshold, color_mask

def preprocess_image(image, color_key_array, callback=print, brightness_adjustment=None):
    """"
    Preprocesses the input image based on color keys and various parameters.

    Parameters:
    - image (np.ndarray): Input image in RGB or RGBA format.
    - color_key_array (list of dict): List containing color keys with 'number', 'hex', 'boost', and 'threshold'.
    - brightness_adjustment (float, optional): Value between 0 and 1 where 0.5 means no brightness adjustment.
                                               If None or invalid, computed using the helper function.
    - callback (function): Function to call for logging/debugging.

    Returns:
    - np.ndarray: Preprocessed image in RGB or RGBA format.
    """
    global use_lab
    auto_color_boost = False

    # Validate and compute brightness_adjustment
    if brightness_adjustment is None or not (0.0 <= brightness_adjustment <= 1.0):
        # Convert to LAB for brightness analysis
        if image.shape[2] == 4:
            rgb_image_for_brightness = image[:, :, :3].astype(np.uint8)
        else:
            rgb_image_for_brightness = image.astype(np.uint8)
        lab_for_brightness = cv2.cvtColor(rgb_image_for_brightness, cv2.COLOR_RGB2LAB)
        brightness_adjustment = compute_brightness_adjustment(lab_for_brightness, use_lab)
        callback(f"Computed brightness_adjustment: {brightness_adjustment}")
    else:
        callback(f"Using provided brightness_adjustment: {brightness_adjustment}")

    # Configuration based on use_lab
    config = {
        'gamma_correction': 0.8 if use_lab else 0.9,
        'clahe_clip_limit': 4.0 if use_lab else 4.0,
        'threshold_default': 20 if use_lab else 28,
        'clahe_grid_size': 8, 
        'unsharp_strength': 3,
        'unsharp_radius': 1.0,
        'contrast_percentiles': (1, 99),
        'alpha_threshold': 191, 
    }

    def calculate_luminance(rgb):
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    # Handle Alpha Channel
    has_alpha = (image.shape[2] == 4)
    if has_alpha:
        alpha_channel = image[:, :, 3]
        rgb_image = image[:, :, :3].astype(np.uint8)
        opaque_mask = alpha_channel >= config['alpha_threshold']
    else:
        alpha_channel = None
        rgb_image = image.astype(np.uint8)
        opaque_mask = None  # Not used if there's no alpha channel

    # Mild Noise Reduction
    rgb_image = cv2.bilateralFilter(rgb_image, d=5, sigmaColor=30, sigmaSpace=30)

    # Convert to LAB for centralized brightness adjustment
    lab_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2LAB)
    L, A, B = cv2.split(lab_image)

    # Apply CLAHE to L Channel
    clahe = cv2.createCLAHE(clipLimit=config['clahe_clip_limit'],
                            tileGridSize=(config['clahe_grid_size'], config['clahe_grid_size']))
    L = clahe.apply(L)
    callback(f"Applied CLAHE with clipLimit: {config['clahe_clip_limit']} and tileGridSize: {config['clahe_grid_size']}")

    # Adjust Brightness
    # 0.5 means no change, <0.5 darkens, >0.5 brightens
    brightness_factor = brightness_adjustment * 2  # Scale to [0, 2], where 1 is no change
    L = np.clip(L * brightness_factor, 0, 255).astype(np.uint8)
    callback(f"Applied brightness adjustment with factor: {brightness_factor}")

    # Merge and Convert Back to RGB
    lab_image = cv2.merge((L, A, B))
    rgb_image = cv2.cvtColor(lab_image, cv2.COLOR_LAB2RGB)


    color_key_luminances = [
        calculate_luminance(tuple(int(ck['hex'][i:i + 2], 16) for i in (0, 2, 4)))
        for ck in color_key_array
    ]
    min_brightness = min(color_key_luminances)
    max_brightness = max(color_key_luminances)
    callback(f"Contrast Stretching: min_brightness={min_brightness}, max_brightness={max_brightness}")

    # Determine opaque pixels
    if has_alpha:
        current_opaque_mask = opaque_mask
    else:
        current_opaque_mask = np.ones_like(rgb_image[:, :, 0], dtype=bool)

    for c in range(3):
        channel = rgb_image[:, :, c]
        lower_percentile, upper_percentile = config['contrast_percentiles']
        min_val = np.percentile(channel[current_opaque_mask], lower_percentile)
        max_val = np.percentile(channel[current_opaque_mask], upper_percentile)
        if max_val > min_val:
            scale = 255.0 / (max_val - min_val) if (max_val - min_val) != 0 else 1.0
            channel = ((channel - min_val) * scale).clip(0, 255).astype(np.uint8)
            rgb_image[:, :, c] = channel
            callback(f"Channel {c} contrast stretched with min_val={min_val}, max_val={max_val}, scale={scale}")

    # Gamma Correction (Common to Both LAB and RGB Paths)
    gamma = config['gamma_correction']
    if gamma != 1.0:
        invGamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(256)]).astype("uint8")
        rgb_image = cv2.LUT(rgb_image, table)
        callback(f"Applied gamma correction with gamma: {gamma}")

    # Convert to HSV for Saturation Adjustment
    hsv_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2HSV)
    h = hsv_image[:, :, 0].astype(np.float32)
    s = hsv_image[:, :, 1].astype(np.float32)

    # Handle Color Boosting
    if auto_color_boost:
        for ckey in color_key_array:
            boost, threshold, color_mask = adjust_boost_threshold(ckey, use_lab, lab_image, hsv_image, config)
            
            # Apply boost to saturation where mask is True
            s[color_mask] = np.clip(s[color_mask] * boost, 0, 255)
            callback(f"Applied saturation boost for color {ckey['hex']} with boost: {boost}")
    else:
        for ckey in color_key_array:
            hex_code = ckey['hex'].lstrip('#')
            color_rgb = tuple(int(hex_code[i:i + 2], 16) for i in (0, 2, 4))
            boost = ckey.get('boost', 1.2)
            threshold = ckey.get('threshold', config['threshold_default'])

            if use_lab:
                # Convert color to LAB
                color_lab = cv2.cvtColor(np.uint8([[color_rgb]]), cv2.COLOR_RGB2LAB)[0, 0]
                # Calculate Euclidean distance in LAB space
                distance = np.linalg.norm(lab_image.astype(np.float32) - color_lab.astype(np.float32), axis=2)
                color_mask = (distance < threshold)
            else:
                # Convert color to HSV
                color_hsv = cv2.cvtColor(np.uint8([[color_rgb]]), cv2.COLOR_RGB2HSV)[0, 0]
                target_h = color_hsv[0]
                hue_diff = np.abs(h - target_h)
                hue_diff = np.minimum(hue_diff, 180 - hue_diff)
                color_mask = (hue_diff < threshold)

            # Apply boost to saturation where mask is True
            s[color_mask] = np.clip(s[color_mask] * boost, 0, 255)
            callback(f"Manual Boost - Applied saturation boost for color {ckey['hex']} with boost: {boost}")

    # Update the HSV image with adjusted saturation
    hsv_image[:, :, 1] = s
    rgb_image = cv2.cvtColor(hsv_image.astype(np.uint8), cv2.COLOR_HSV2RGB)

    # Unsharp Masking (Optional)
    unsharp_strength = config['unsharp_strength']
    if unsharp_strength > 0:
        blurred = cv2.GaussianBlur(rgb_image, (0, 0), config['unsharp_radius'])
        sharpened = cv2.addWeighted(rgb_image, 1 + unsharp_strength, blurred, -unsharp_strength, 0)
        rgb_image = sharpened
        callback(f"Applied unsharp masking with strength: {unsharp_strength}")

    # Reintegrate Alpha Channel if Present
    if has_alpha:
        # Ensure RGB image is uint8
        rgb_image = rgb_image.astype(np.uint8)
        # Restore original RGB values for non-opaque pixels
        if opaque_mask is not None:
            rgb_image[~opaque_mask] = image[:, :, :3][~opaque_mask]
        # Merge with original alpha channel
        preprocessed_image = np.dstack((rgb_image, alpha_channel))
    else:
        preprocessed_image = rgb_image

    return preprocessed_image

def crop_to_solid_area(image: Image.Image) -> Image.Image:
    """
    Crops an RGBA image to remove fully transparent (alpha=0) areas around the solid pixels.
    
    Args:
        image (Image.Image): Input image in RGBA mode.

    Returns:
        Image.Image: Cropped image containing only solid pixels.
    """
    if image.mode != "RGBA":
        raise ValueError("Image must be in RGBA mode")

    # Extract the alpha channel
    alpha = image.split()[3]  # The fourth channel is alpha in RGBA

    # Get the bounding box of the non-transparent area
    bbox = alpha.getbbox()
    if bbox:
        # Crop the image to the bounding box
        cropped_image = image.crop(bbox)
        return cropped_image
    else:
        # If the entire image is transparent, return an empty (1x1) RGBA image
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def resize_image(img, target_size):
    """
    Resizes the image to the target size while maintaining aspect ratio.
    Handles the alpha channel separately to prevent artifacting.
    """
    try:
        width, height = img.size
        scale_factor = target_size / float(max(width, height))
        new_width = max(1, int(width * scale_factor))
        new_height = max(1, int(height * scale_factor))

        # Separate the alpha channel
        if img.mode == 'RGBA':
            img_no_alpha = img.convert('RGB')
            alpha = img.getchannel('A')

            # Resize RGB and alpha channels separately
            img_no_alpha = img_no_alpha.resize((new_width, new_height), resample=Image.LANCZOS)
            alpha = alpha.resize((new_width, new_height), resample=Image.LANCZOS)

            # Merge back together
            img = Image.merge('RGBA', (*img_no_alpha.split(), alpha))
        else:
            img = img.resize((new_width, new_height), resample=Image.LANCZOS)

        return img
    except Exception as e:
        raise RuntimeError(f"Failed to resize the image: {e}")

def process_image(img, color_key, process_mode, process_params):
    """
    Dispatches image processing to the appropriate method based on process_mode.
    """
    global use_lab
    img = img.copy()
    if process_mode in processing_method_registry:
        processing_function = processing_method_registry[process_mode]
        return processing_function(img, color_key, process_params)
    else:
        # Default to color matching if unknown process_mode
        return color_matching(img, color_key, process_params)


@register_processing_method(
    'Color Match',
    default_params={},
    description="Maps each pixel to the closest color of chalk. Basic, consistent, and reliable."
)
def color_matching(img, color_key, params):
    img_array = np.array(img)
    has_alpha = (img_array.shape[2] == 4) if img_array.ndim == 3 else False
    alpha_threshold = 191

    if has_alpha:
        alpha_channel = img_array[:, :, 3]
        opaque_mask = (alpha_channel > alpha_threshold)
    else:
        opaque_mask = np.ones((img_array.shape[0], img_array.shape[1]), dtype=bool)

    mapped = find_closest_colors_image(img_array, color_key)

    if has_alpha:
        mapped[~opaque_mask] = img_array[~opaque_mask]

    mode = 'RGBA' if has_alpha else 'RGB'
    result_img = Image.fromarray(mapped, mode=mode)
    return result_img



@register_processing_method(
    'K-Means Mapping',
    default_params={'Clusters': 12},
    description="Simplify complex images to be less noisy! Use slider to adjust the amount of color groups. Great for limited palette."
)
def simple_k_means_palette_mapping(img, color_key, params):
    has_alpha = (img.mode == 'RGBA')
    if has_alpha:
        alpha_channel = np.array(img.getchannel('A'))
        rgb_img = img.convert('RGB')
    else:
        alpha_channel = None
        rgb_img = img

    data = np.array(rgb_img)
    data_flat = data.reshape((-1, 3))

    clusters = params['Clusters']
    if clusters == 16:
        clusters = 24

    # Use threading backend with joblib to prevent CMD windows
    with parallel_backend('threading'):
        kmeans = KMeans(
            n_clusters=clusters,
            init="k-means++",
            n_init=10,
            random_state=0
        ).fit(data_flat)

    cluster_centers = kmeans.cluster_centers_
    labels = kmeans.labels_

    # Map each cluster center to closest palette color individually
    cluster_map = {}
    for i, center in enumerate(cluster_centers):
        center_rgb = tuple(center.astype(np.uint8))
        c_idx = find_closest_color(center_rgb, color_key)
        cluster_map[i] = color_key[c_idx]

    mapped_flat = np.array([cluster_map[label] for label in labels], dtype=np.uint8)
    mapped_data = mapped_flat.reshape(data.shape)

    if has_alpha:
        rgba_data = np.dstack((mapped_data, alpha_channel))
        result_img = Image.fromarray(rgba_data, 'RGBA')
    else:
        result_img = Image.fromarray(mapped_data, 'RGB')

    return result_img

@register_processing_method(
    'Hybrid Dither',
    default_params={'strength': 1.0},
    description="Switches between Atkinson and Floyd dithering based on texture."
)
def hybrid_dithering(img, color_key, params):
    global use_lab

    strength = params.get('strength', 1.0)

    gray_img = img.convert('L')
    edges = gray_img.filter(ImageFilter.FIND_EDGES)
    saliency_map = edges.filter(ImageFilter.GaussianBlur(1.5))
    saliency_array = np.array(saliency_map, dtype=np.float32) / 255.0

    has_alpha = (img.mode == 'RGBA')
    img_array = np.array(img, dtype=np.uint8)
    height, width = img_array.shape[:2]

    if has_alpha:
        alpha_channel = img_array[:, :, 3]
    else:
        alpha_channel = None

    color_nums = list(color_key.keys())
    color_values_rgb = np.array(list(color_key.values()), dtype=np.uint8)
    if use_lab:
        color_values_lab = rgb_palette_to_lab(color_key).astype(np.float32)
    else:
        color_values_lab = None

    def closest_color_func(pixel_rgb):
        if use_lab:
            arr = np.uint8([[pixel_rgb]])
            p_lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0,0].astype(np.float32)
            diff = color_values_lab - p_lab
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        else:
            p_f = np.float32(pixel_rgb)
            diff = color_values_rgb.astype(np.float32) - p_f
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        return idx

    atkinson_matrix = [
        (1, 0, 1 / 8), (2, 0, 1 / 8),
        (-1, 1, 1 / 8), (0, 1, 1 / 8), (1, 1, 1 / 8),
        (0, 2, 1 / 8),
    ]
    floyd_steinberg_matrix = [
        (1, 0, 7 / 16),
        (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16),
    ]

    img_array = img_array.astype(np.int16)

    if has_alpha:
        alpha_mask = (alpha_channel > 0)
    else:
        alpha_mask = np.ones((height, width), dtype=bool)

    for y in range(height):
        for x in range(width):
            if not alpha_mask[y, x]:
                continue

            old_pixel = img_array[y, x]
            old_pixel_rgb = old_pixel[:3]

            saliency = saliency_array[y, x]
            diffusion_matrix = floyd_steinberg_matrix if saliency > 0.5 else atkinson_matrix

            idx = closest_color_func(old_pixel_rgb)
            new_pixel = color_values_rgb[idx]

            quant_error = [(o - n) * strength for o, n in zip(old_pixel_rgb, new_pixel)]

            if has_alpha:
                img_array[y, x, :3] = new_pixel
                img_array[y, x, 3] = old_pixel[3]
            else:
                img_array[y, x, :3] = new_pixel

            for dx, dy, coeff in diffusion_matrix:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and alpha_mask[ny, nx]:
                    neighbor = img_array[ny, nx]
                    nr = neighbor[0] + quant_error[0]*coeff
                    ng = neighbor[1] + quant_error[1]*coeff
                    nb = neighbor[2] + quant_error[2]*coeff
                    img_array[ny, nx, 0] = int(min(max(nr, 0), 255))
                    img_array[ny, nx, 1] = int(min(max(ng, 0), 255))
                    img_array[ny, nx, 2] = int(min(max(nb, 0), 255))

    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    if has_alpha:
        result_img = Image.fromarray(img_array, 'RGBA')
    else:
        result_img = Image.fromarray(img_array, 'RGB')

    return result_img

@register_processing_method(
    'Pattern Dither',
    default_params={'strength': 0.75},
    description="Uses an 8x8 Bayer matrix to apply dithering in a pattern. Pretty :3"
)
def ordered_dithering(img, color_key, params):
    global use_lab

    strength = params.get('strength', 1.0)
    bayer_8x8 = np.array([
        [0,32,8,40,2,34,10,42],
        [48,16,56,24,50,18,58,26],
        [12,44,4,36,14,46,6,38],
        [60,28,52,20,62,30,54,22],
        [3,35,11,43,1,33,9,41],
        [51,19,59,27,49,17,57,25],
        [15,47,7,39,13,45,5,37],
        [63,31,55,23,61,29,53,21]
    ], dtype=np.float32) / 64.0

    img = img.copy()
    img_array = np.array(img, dtype=np.uint8)
    has_alpha = (img.mode == 'RGBA')
    if has_alpha:
        alpha_channel = img_array[:, :, 3]
        alpha_mask = (alpha_channel > 0)
    else:
        alpha_mask = np.ones((img_array.shape[0], img_array.shape[1]), dtype=bool)

    height, width = img_array.shape[:2]

    color_nums = list(color_key.keys())
    color_values_rgb = np.array(list(color_key.values()), dtype=np.uint8)
    if use_lab:
        color_values_lab = rgb_palette_to_lab(color_key).astype(np.float32)
    else:
        color_values_lab = None

    def closest_color_func(pixel_rgb):
        if use_lab:
            arr = np.uint8([[pixel_rgb]])
            p_lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0,0].astype(np.float32)
            diff = color_values_lab - p_lab
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        else:
            p_f = pixel_rgb.astype(np.float32)
            diff = color_values_rgb.astype(np.float32) - p_f
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        return idx

    adjustment_factor = 0.3 * strength

    tiled_bayer = np.tile(bayer_8x8, (height//8+1, width//8+1))
    tiled_bayer = tiled_bayer[:height, :width]

    img_array = img_array.astype(np.int16)

    for y in range(height):
        for x in range(width):
            if not alpha_mask[y, x]:
                continue

            old_pixel = img_array[y, x, :3].astype(np.uint8)

            if use_lab:
                p_lab = rgb_to_lab_single(old_pixel)
                L = p_lab[0] / 255.0
                if L < tiled_bayer[y, x]:
                    L_adj = max(0, L - adjustment_factor)
                else:
                    L_adj = min(1.0, L + adjustment_factor)

                p_lab_adj = (L_adj*255, p_lab[1], p_lab[2])
                arr = np.uint8([[p_lab_adj]])
                pixel_rgb_adj = cv2.cvtColor(arr, cv2.COLOR_LAB2RGB)[0,0]
            else:
                R, G, B = old_pixel
                brightness = (R+G+B)/765.0
                if brightness < tiled_bayer[y, x]:
                    factor = 1 - adjustment_factor
                else:
                    factor = 1 + adjustment_factor
                pixel_rgb_adj = np.clip([R*factor, G*factor, B*factor],0,255).astype(np.uint8)

            idx = closest_color_func(pixel_rgb_adj)
            new_pixel = color_values_rgb[idx]
            img_array[y, x, :3] = new_pixel

    img_array = np.clip(img_array, 0, 255).astype(np.uint8)
    if has_alpha:
        result_img = Image.fromarray(img_array, 'RGBA')
    else:
        result_img = Image.fromarray(img_array, 'RGB')

    return result_img


@register_processing_method(
    'Atkinson Dither',
    default_params={'strength': 1.0},
    description="Dithering suited for smaller images! Used by the Macintosh to translate images for monochrome displays."
)
def atkinson_dithering(img, color_key, params):
    strength = params.get('strength', 1.0)
    diffusion_matrix = [
        (1, 0, 1 / 8), (2, 0, 1 / 8),
        (-1, 1, 1 / 8), (0, 1, 1 / 8), (1, 1, 1 / 8),
        (0, 2, 1 / 8),
    ]
    return optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix)


@register_processing_method(
    'Stucki Dither',
    default_params={'strength': 1.0},
    description="An enhancement of Floyd-Steinberg with a wider diffusion matrix for less noisy results."
)
def stucki_dithering(img, color_key, params):
    strength = params.get('strength', 1.0)
    diffusion_matrix = [
        (1, 0, 8 / 42), (2, 0, 4 / 42),
        (-2, 1, 2 / 42), (-1, 1, 4 / 42), (0, 1, 8 / 42), (1, 1, 4 / 42), (2, 1, 2 / 42),
        (-2, 2, 1 / 42), (-1, 2, 2 / 42), (0, 2, 4 / 42), (1, 2, 2 / 42), (2, 2, 1 / 42),
    ]
    return optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix)


@register_processing_method(
    'Floyd Dither',
    default_params={'strength': 1.0},
    description="Create smooth gradients using diffusion. Best used for images with size over ~120."
)
def floyd_steinberg_dithering(img, color_key, params):
    strength = params.get('strength', 1.0)
    diffusion_matrix = [
        (1, 0, 7 / 16),
        (-1, 1, 3 / 16),
        (0, 1, 5 / 16),
        (1, 1, 1 / 16),
    ]
    return optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix)


@register_processing_method(
    'Jarvis Dither',
    default_params={'strength': 1.0},
    description="Applies diffusion over a large area. Best used for images with size over ~120."
)
def jarvis_judice_ninke_dithering(img, color_key, params):
    strength = params.get('strength', 1.0)
    diffusion_matrix = [
        (1, 0, 7 / 48), (2, 0, 5 / 48),
        (-2, 1, 3 / 48), (-1, 1, 5 / 48), (0, 1, 7 / 48), (1, 1, 5 / 48), (2, 1, 3 / 48),
        (-2, 2, 1 / 48), (-1, 2, 3 / 48), (0, 2, 5 / 48), (1, 2, 3 / 48), (2, 2, 1 / 48),
    ]
    return optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix)


@register_processing_method(
    'Sierra Dither',
    default_params={'strength': 1.0},
    description="A Sierra variant that provides smooth gradients with less computational complexity."
)
def sierra2_dithering(img, color_key, params):
    strength = params.get('strength', 1.0)
    diffusion_matrix = [
        (1, 0, 4/16), (2, 0, 3/16),
        (-2, 1, 1/16), (-1, 1, 2/16), (0, 1, 3/16), (1, 1, 2/16), (2, 1, 1/16),
    ]
    return optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix)

@register_processing_method(
    'Random Dither',
    default_params={'strength': 1.0},
    description="Adds randomized dithering for a noisier but more natural texture."
)
def random_dithering(img, color_key, params):
    global use_lab

    strength = params.get('strength', 1.0)

    img = img.copy()
    width, height = img.size
    pixels = img.load()
    has_alpha = img.mode == 'RGBA'

    noise_std = 32 * strength

    def rgb_to_lab_single(rgb):
        arr = np.uint8([[rgb]])
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0, 0]
        return lab

    def lab_to_rgb_single(lab):
        arr = np.uint8([[lab]])
        rgb = cv2.cvtColor(arr, cv2.COLOR_LAB2RGB)[0, 0]
        return rgb

    for y in range(height):
        for x in range(width):
            old_pixel = pixels[x, y]
            if has_alpha and old_pixel[3] == 0:
                continue

            old_pixel_rgb = old_pixel[:3]

            if use_lab:
                old_pixel_lab = rgb_to_lab_single(old_pixel_rgb)
                l_noise = np.clip(old_pixel_lab[0] + np.random.normal(0, noise_std*0.5), 0, 255)
                a_noise = np.clip(old_pixel_lab[1] + np.random.normal(0, noise_std*0.25), 0, 255)
                b_noise = np.clip(old_pixel_lab[2] + np.random.normal(0, noise_std*0.25), 0, 255)

                noisy_lab = (l_noise, a_noise, b_noise)
                noisy_pixel = lab_to_rgb_single(noisy_lab)
            else:
                r = np.clip(old_pixel_rgb[0] + np.random.normal(0, noise_std), 0, 255)
                g = np.clip(old_pixel_rgb[1] + np.random.normal(0, noise_std), 0, 255)
                b = np.clip(old_pixel_rgb[2] + np.random.normal(0, noise_std), 0, 255)
                noisy_pixel = (r, g, b)

            noisy_pixel = tuple(int(c) for c in noisy_pixel)
            new_pixel_num = find_closest_color(noisy_pixel, color_key)
            new_pixel = color_key[new_pixel_num]

            if has_alpha:
                pixels[x, y] = new_pixel + (old_pixel[3],)
            else:
                pixels[x, y] = new_pixel

    return img


def build_color_key(color_key_array):
    color_key = {}
    for item in color_key_array:
        color_num = item['number']
        hex_code = item['hex'].lstrip('#')
        rgb = tuple(int(hex_code[i:i+2], 16) for i in (0, 2, 4))
        color_key[color_num] = rgb
    return color_key

def rgb_to_lab_single(rgb):
    arr = np.uint8([[rgb]])
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0,0]
    return lab

def rgb_palette_to_lab(color_key):
    rgb_vals = np.array(list(color_key.values()), dtype=np.uint8)
    rgb_vals_reshaped = rgb_vals.reshape(-1,1,3)
    lab_vals = cv2.cvtColor(rgb_vals_reshaped, cv2.COLOR_RGB2LAB)
    lab_vals = lab_vals.reshape(-1,3)
    return lab_vals

def find_closest_color(pixel, color_key):
    global use_lab
    pixel = np.array(pixel, dtype=np.uint8)

    color_nums = list(color_key.keys())
    color_values_rgb = np.array(list(color_key.values()), dtype=np.uint8)

    if use_lab:
        pixel_lab = rgb_to_lab_single(pixel)
        color_values_lab = rgb_palette_to_lab(color_key)
        diff = color_values_lab.astype(np.float32) - pixel_lab.astype(np.float32)
        dist_sq = np.sum(diff**2, axis=1)
        idx = np.argmin(dist_sq)
    else:
        pixel_f = pixel.astype(np.float32)
        colors_f = color_values_rgb.astype(np.float32)
        diff = colors_f - pixel_f
        dist_sq = np.sum(diff**2, axis=1)
        idx = np.argmin(dist_sq)

    return color_nums[idx]

def find_closest_colors_image(image_array, color_key):
    global use_lab

    has_alpha = (image_array.shape[2] == 4)
    if has_alpha:
        rgb_data = image_array[:, :, :3]
        alpha_channel = image_array[:, :, 3]
    else:
        rgb_data = image_array

    H, W = rgb_data.shape[:2]

    color_nums = list(color_key.keys())
    color_values_rgb = np.array(list(color_key.values()), dtype=np.uint8)

    if use_lab:
        rgb_data_reshaped = rgb_data.reshape(-1, 1, 3)
        lab_data = cv2.cvtColor(rgb_data_reshaped, cv2.COLOR_RGB2LAB)
        lab_data = lab_data.reshape(H, W, 3)
        color_values_lab = rgb_palette_to_lab(color_key).astype(np.float32)
        lab_flat = lab_data.reshape(-1, 3).astype(np.float32)
        diff = lab_flat[:, None, :] - color_values_lab[None, :, :]
        dist_sq = np.sum(diff**2, axis=2)
        closest_indices = np.argmin(dist_sq, axis=1)
        mapped_flat = color_values_rgb[closest_indices]
    else:
        flat_rgb = rgb_data.reshape(-1, 3).astype(np.float32)
        colors_f = color_values_rgb.astype(np.float32)
        diff = flat_rgb[:, None, :] - colors_f[None, :, :]
        dist_sq = np.sum(diff**2, axis=2)
        closest_indices = np.argmin(dist_sq, axis=1)
        mapped_flat = color_values_rgb[closest_indices]

    mapped_data = mapped_flat.reshape(H, W, 3).astype(np.uint8)

    if has_alpha:
        mapped_data = np.dstack((mapped_data, alpha_channel))

    return mapped_data

def distribute_error(pixels, x, y, width, height, quant_error, diffusion_matrix):
    for dx, dy, coefficient in diffusion_matrix:
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height:
            current_pixel = list(pixels[nx, ny])
            has_alpha = (len(current_pixel) == 4)
            if has_alpha and current_pixel[3] == 0:
                continue

            for i in range(3):
                val = current_pixel[i] + quant_error[i] * coefficient
                current_pixel[i] = int(min(max(val, 0), 255))
            pixels[nx, ny] = tuple(current_pixel)

def optimized_error_diffusion_dithering(img, color_key, strength, diffusion_matrix):
    global use_lab

    img = img.copy()
    has_alpha = (img.mode == 'RGBA')
    img_array = np.array(img, dtype=np.uint8)
    height, width = img_array.shape[:2]

    if has_alpha:
        alpha_channel = img_array[:, :, 3]
        alpha_mask = (alpha_channel > 0)
    else:
        alpha_mask = np.ones((height, width), dtype=bool)

    color_nums = list(color_key.keys())
    color_values_rgb = np.array(list(color_key.values()), dtype=np.uint8)
    if use_lab:
        color_values_lab = rgb_palette_to_lab(color_key).astype(np.float32)
    else:
        color_values_lab = None

    def closest_color_func(pixel_rgb):
        if use_lab:
            arr = np.uint8([[pixel_rgb]])
            p_lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0,0].astype(np.float32)
            diff = color_values_lab - p_lab
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        else:
            p_f = pixel_rgb.astype(np.float32)
            diff = color_values_rgb.astype(np.float32) - p_f
            dist_sq = np.sum(diff*diff, axis=1)
            idx = np.argmin(dist_sq)
        return idx

    img_array = img_array.astype(np.int16)

    for y in range(height):
        for x in range(width):
            if not alpha_mask[y, x]:
                continue

            old_pixel = img_array[y, x]
            old_pixel_rgb = old_pixel[:3]

            idx = closest_color_func(old_pixel_rgb)
            new_pixel = color_values_rgb[idx]

            quant_error = [(o - n)*strength for o, n in zip(old_pixel_rgb, new_pixel)]

            if has_alpha:
                img_array[y, x, 0] = new_pixel[0]
                img_array[y, x, 1] = new_pixel[1]
                img_array[y, x, 2] = new_pixel[2]
            else:
                img_array[y, x, :3] = new_pixel

            for dx, dy, coeff in diffusion_matrix:
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and alpha_mask[ny, nx]:
                    neighbor = img_array[ny, nx]
                    nr = neighbor[0] + quant_error[0]*coeff
                    ng = neighbor[1] + quant_error[1]*coeff
                    nb = neighbor[2] + quant_error[2]*coeff
                    img_array[ny, nx, 0] = int(min(max(nr, 0), 255))
                    img_array[ny, nx, 1] = int(min(max(ng, 0), 255))
                    img_array[ny, nx, 2] = int(min(max(nb, 0), 255))

    img_array = np.clip(img_array, 0, 255).astype(np.uint8)

    if has_alpha:
        result_img = Image.fromarray(img_array, 'RGBA')
    else:
        result_img = Image.fromarray(img_array, 'RGB')

    return result_img

def error_diffusion_dithering(img, color_key, strength, diffusion_matrix, smoothing=False):
    width, height = img.size
    pixels = img.load()
    has_alpha = img.mode == 'RGBA'

    for y in range(height):
        for x in range(width):
            old_pixel = pixels[x, y]
            if has_alpha and old_pixel[3] == 0:
                continue
            old_pixel_rgb = old_pixel[:3]

            new_pixel_num = find_closest_color(old_pixel_rgb, color_key)
            new_pixel = color_key[new_pixel_num]

            quant_error = tuple((o - n) * strength for o, n in zip(old_pixel_rgb, new_pixel))

            if has_alpha:
                pixels[x, y] = new_pixel + (old_pixel[3],)
            else:
                pixels[x, y] = new_pixel

            distribute_error(pixels, x, y, width, height, quant_error, diffusion_matrix)

    return img
#Processing End




def process_and_save_image(img, target_size, process_mode, use_lab_flag, process_params, color_key_array, remove_bg, preprocess_flag, progress_callback=None, message_callback=None, error_callback=None):
    """
    Processes and saves the image according to specified parameters.
    Ensures that only pixels with alpha > 191 are included in the output and preview.
    """
    try:
        
        if message_callback:
            message_callback("Preparing image...")

        # Prepare the image (convert to RGBA if needed)
        img = prepare_image(img)

        if remove_bg:
            if message_callback:
                message_callback("Attemting Background Removal!")
            img = remove_background(img, message_callback)
        # Resize the image if needed
        if target_size is not None:
            img = resize_image(img, target_size)
            if message_callback:
                message_callback(f"Image resized to {img.size}")
        else:
            if message_callback:
                message_callback("Keeping original image dimensions.")


        # Preprocess the image if needed
        if preprocess_flag:
            img_np = np.array(img)
            img_np = preprocess_image(img_np, color_key_array, message_callback)
            if message_callback:
                message_callback("Image preprocessed.")
            img = Image.fromarray(img_np, 'RGBA')

        # Construct color_key from color_key_array
        color_key = build_color_key(color_key_array)
        
        img = process_image(img, color_key, process_mode, process_params)

        if message_callback:
            message_callback(f"Processing applied: {process_mode}")
            
        
        # Save a preview of the processed image in 'preview' folder
        create_and_clear_preview_folder(message_callback)

        # Apply transparency filtering for the preview
        img = img.copy()
        pixels = img.load()
        width, height = img.size
        for y in range(height):
            for x in range(width):
                pixel = pixels[x, y]
                if len(pixel) == 4 and pixel[3] <= 191:  # RGBA and alpha <= 191
                    pixels[x, y] = (0, 0, 0, 0)  # Make fully transparent

        # Save the preview image
        img = crop_to_solid_area(img)
        
        preview_path = exe_path_fs('game_data/stamp_preview/preview.png')
        img.save(preview_path)
        if message_callback:
            message_callback(f"Preview saved at: {preview_path}")

        # Save the processed image data to stamp.txt
        width, height = img.size
        scaled_width = round(width * 0.1, 1)
        scaled_height = round(height * 0.1, 1)

        current_dir = exe_path_fs('game_data/current_stamp_data')
        os.makedirs(current_dir, exist_ok=True)  # Ensure the directory exists
        output_file_path = exe_path_fs('game_data/current_stamp_data/stamp.txt')

        with open(output_file_path, 'w') as f:
            # Write the first line with scaled width, height, and 'img'
            f.write(f"{scaled_width},{scaled_height},img\n")
            if message_callback:
                message_callback(f"Scaled dimensions written: {scaled_width},{scaled_height},img")

            # Process each pixel
            pixels = img.load()
            for y in range(height - 1, -1, -1):  # Process from bottom to top
                for x in range(width):
                    try:
                        pixel = pixels[x, y]

                        # Handle both RGB and RGBA images
                        if len(pixel) == 4:  # RGBA
                            r, g, b, a = pixel
                        elif len(pixel) == 3:  # RGB
                            r, g, b = pixel
                            a = 255  # Assume fully opaque
                        else:
                            raise ValueError(f"Unexpected pixel format at ({x}, {y}): {pixel}")

                        if a <= 191:  # Skip pixels with alpha <= 191 (75% opacity)
                            continue

                        # Map the pixel to the closest color
                        closest_color_num = find_closest_color((r, g, b), color_key)

                        # Scale the coordinates
                        scaled_x = round(x * 0.1, 1)
                        scaled_y = round((height - 1 - y) * 0.1, 1)

                        # Write to the file
                        f.write(f"{scaled_x},{scaled_y},{closest_color_num}\n")

                    except Exception as e:
                        if message_callback:
                            message_callback(f"Error processing pixel at ({x}, {y}): {e}")

        if message_callback:
            message_callback(f"Processing complete! Output saved to: {output_file_path}")

    except Exception as e:
        if error_callback:
            error_callback(f"An error occurred: {e}")



def process_and_save_gif(image_path, target_size, process_mode, use_lab_flag, process_params, color_key_array, remove_bg, preprocess_flag,
                         progress_callback=None, message_callback=None, error_callback=None):
    """
    Processes and saves an animated image (GIF or WebP) according to specified parameters.
    """
    try:
        
        # Open frames.txt and clear its contents
        current_dir = exe_path_fs('game_data/current_stamp_data')
        os.makedirs(current_dir, exist_ok=True)  # Ensure the directory exists
        frames_txt_path = exe_path_fs('game_data/current_stamp_data/frames.txt')
        with open(frames_txt_path, 'w') as frames_file:
            pass  # Clears the file

        # Open the image
        img = Image.open(image_path)
        if not getattr(img, "is_animated", False):
            if message_callback:
                message_callback("Selected file is not an animated image with multiple frames.")
            img.close()
            return

        total_frames = img.n_frames
        if message_callback:
            message_callback(f"Total frames in image: {total_frames}")

        # Gather delays for each frame
        delays = []
        for frame_number in range(total_frames):
            img.seek(frame_number)
            delay = img.info.get('duration', 100)  # Default to 100ms if not specified
            delays.append(delay)

        # Determine delay uniformity
        uniform_delay = delays[0] if all(d == delays[0] for d in delays) else -1

        # Save frames to 'Frames' directory (and clear it first)
        save_frames(img, target_size, process_mode, use_lab_flag, process_params, remove_bg, preprocess_flag, color_key_array,
                    progress_callback, message_callback, error_callback)

        # Create and clear 'preview' folder
        preview_folder = create_and_clear_preview_folder(message_callback)

        # Now process frames and write to frames.txt
        # Load the first frame
        first_frame_path = exe_path_fs('game_data/frames/frame_1.png')

        if not first_frame_path.exists:
            if error_callback:
                error_callback(f"First frame not found at {first_frame_path}")
            img.close()
            return

        # Construct color_key from color_key_array
        color_key = build_color_key(color_key_array)
       

        # Process first frame and write to stamp.txt
        with Image.open(first_frame_path) as first_frame:
            width, height = first_frame.size
            scaled_width = round(width * 0.1, 1)  # Multiply by 0.1
            scaled_height = round(height * 0.1, 1)  # Multiply by 0.1

            # Write header to stamp.txt
            current_dir = exe_path_fs('game_data/current_stamp_data')
            os.makedirs(current_dir, exist_ok=True)  # Ensure the directory exists
            stamp_txt_path = current_dir / 'stamp.txt'
            with open(stamp_txt_path, 'w') as stamp_file:
                stamp_file.write(f"{scaled_width},{scaled_height},gif,{total_frames},{uniform_delay}\n")
                if message_callback:
                    message_callback(f"Header written to stamp.txt: {scaled_width},{scaled_height},gif,{total_frames},{uniform_delay}")

                # Initialize Frame1Array and store the first frame's pixels
                Frame1Array = {}  # Dictionary to store pixels as {(x, y): color_num}
                first_frame_pixels = {}  # Store for looping comparison

                pixels = first_frame.load()
                for y in range(height):
                    for x in range(width):
                        pixel = pixels[x, y]
                        if len(pixel) == 4:  # RGBA
                            r, g, b, a = pixel
                            if a <= 191:
                                continue  # Skip pixels with alpha <= 191
                        else:
                            r, g, b = pixel
                            a = 255  # Assume fully opaque
                            if a <= 191:
                                continue  # Skip pixels with alpha <= 191

                        # Map the pixel to the closest color
                        closest_color_num = find_closest_color((r, g, b), color_key)
                        Frame1Array[(x, y)] = closest_color_num
                        first_frame_pixels[(x, y)] = closest_color_num  # Store for looping comparison

                        # Scale the coordinates
                        scaled_x = round(x * 0.1, 1)
                        scaled_y = round(y * 0.1, 1)

                        # Write to stamp.txt
                        stamp_file.write(f"{scaled_x},{scaled_y},{closest_color_num}\n")

        # Process subsequent frames
        header_frame_number = 1  # Start header numbering from 1

        for frame_number in range(2, total_frames + 1):  # Start from frame 2
            frame_path = exe_path_fs(f'game_data/frames/frame_{frame_number}.png')
            if not frame_path.exists:
                if message_callback:
                    message_callback(f"Frame {frame_number} not found at {frame_path}")
                continue

            with Image.open(frame_path) as frame:
                pixels = frame.load()
                current_frame_pixels = {}

                # Collect pixels in current frame
                for y in range(height):
                    for x in range(width):
                        pixel = pixels[x, y]
                        if len(pixel) == 4:
                            r, g, b, a = pixel
                            if a <= 191:
                                current_color_num = -1  # Treat as transparent
                            else:
                                current_color_num = find_closest_color((r, g, b), color_key)
                        else:
                            r, g, b = pixel
                            a = 255  # Assume fully opaque
                            if a <= 191:
                                current_color_num = -1  # Treat as transparent
                            else:
                                current_color_num = find_closest_color((r, g, b), color_key)

                        current_frame_pixels[(x, y)] = current_color_num

                # Compare with Frame1Array and find differences
                diffs = []
                all_positions = set(Frame1Array.keys()) | set(current_frame_pixels.keys())
                for (x, y) in all_positions:
                    prev_color_num = Frame1Array.get((x, y), -1)
                    current_color_num = current_frame_pixels.get((x, y), -1)

                    if current_color_num != prev_color_num:
                        if current_color_num == -1:
                            # Pixel became transparent
                            diffs.append((x, y, -1))
                        else:
                            # Pixel changed color or became visible
                            diffs.append((x, y, current_color_num))

                # Write header and diffs to frames.txt
                with open(frames_txt_path, 'a') as frames_file:
                    # Include delay if variable delays
                    if uniform_delay == -1:
                        frame_delay = delays[frame_number - 1]
                        frames_file.write(f"frame,{header_frame_number},{frame_delay}\n")
                    else:
                        frames_file.write(f"frame,{header_frame_number}\n")
                    for x, y, color_num in diffs:
                        # Scale coordinates by multiplying by 0.1
                        scaled_x = round(x * 0.1, 1)
                        scaled_y = round(y * 0.1, 1)
                        frames_file.write(f"{scaled_x},{scaled_y},{color_num}\n")

                # Update Frame1Array
                Frame1Array = current_frame_pixels.copy()

            if progress_callback:
                progress = (frame_number - 1) / total_frames * 100
                progress_callback(progress)

            header_frame_number += 1  # Increment header frame number

        # After processing all frames, compare last frame to first frame to complete the loop
        # Compare Frame1Array (last frame) with first_frame_pixels (first frame)
        diffs = []
        all_positions = set(first_frame_pixels.keys()) | set(Frame1Array.keys())

        for (x, y) in all_positions:
            first_color_num = first_frame_pixels.get((x, y), -1)
            last_color_num = Frame1Array.get((x, y), -1)

            if first_color_num != last_color_num:
                if first_color_num == -1:
                    # Pixel became visible in first frame
                    diffs.append((x, y, first_color_num))
                elif last_color_num == -1:
                    # Pixel became transparent
                    diffs.append((x, y, -1))
                else:
                    # Pixel changed color
                    diffs.append((x, y, first_color_num))

        # Write header and diffs to frames.txt
        with open(frames_txt_path, 'a') as frames_file:
            # Include delay if variable delays
            final_frame_number = header_frame_number
            if uniform_delay == -1:
                frame_delay = delays[0]  # Use the delay of the first frame
                frames_file.write(f"frame,{final_frame_number},{frame_delay}\n")
            else:
                frames_file.write(f"frame,{final_frame_number}\n")
            for x, y, color_num in diffs:
                # Scale coordinates by multiplying by 0.1
                scaled_x = round(x * 0.1, 1)
                scaled_y = round(y * 0.1, 1)
                frames_file.write(f"{scaled_x},{scaled_y},{color_num}\n")

        if message_callback:
            message_callback(f"Processing of animated image frames complete! Data saved to: {frames_txt_path}")


        # Generate preview GIF after all processing is done
        create_preview_gif(total_frames, delays, preview_folder, progress_callback, message_callback, error_callback)

        img.close()  # Close the image after processing

    except Exception as e:
        if error_callback:
            error_callback(f"An error occurred in process_and_save_gif: {e}")
            
def save_frames(img, target_size, process_mode, use_lab_flag, process_params, remove_bg, preprocess_flag, color_key_array,
                progress_callback, message_callback, error_callback):
    """
    Saves each frame of the animated image to the 'Frames' directory after preprocessing, resizing, and applying the selected processing method.
    """
    try:

        output_folder = exe_path_fs("game_data/Frames")
        os.makedirs(output_folder, exist_ok=True)

        # Delete the contents of the 'Frames' folder before starting
        for filename in os.listdir(output_folder):
            file_path = output_folder / filename
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
            except Exception as e:
                if message_callback:
                    message_callback(f'Failed to delete {file_path}. Reason: {e}')

        total_frames = img.n_frames
        if message_callback:
            message_callback(f"Processing and saving {total_frames} frames...")

        # Construct color_key from color_key_array
        color_key = build_color_key(color_key_array)
     
        

        for frame_number in range(1, total_frames + 1):  # Start frame numbering from 1
            img.seek(frame_number - 1)
            frame = img.copy()  # Ensure we have a writable copy of the frame

            # Prepare the image (convert to RGBA if needed)
            frame = prepare_image(frame)

            if remove_bg:
                frame = remove_background(frame, message_callback)
            # Resize the image if needed
            if target_size is not None:
                frame = resize_image(frame, target_size)

            # Preprocess the image if needed
            if preprocess_flag:
                frame_np = np.array(frame)
                frame_np = preprocess_image(frame_np, color_key_array, message_callback)
                frame = Image.fromarray(frame_np, 'RGBA')

            # Apply the processing method to the frame
            frame = process_image(frame, color_key, process_mode, process_params)
            # Handle translucent pixels by creating a writable copy
            if frame.mode != 'RGBA':
                frame = frame.convert('RGBA')  # Ensure image is in RGBA mode
            else:
                frame = frame.copy()  # Make a writable copy if it's already RGBA

            pixels = frame.load()
            width, height = frame.size
            opacity_threshold = 204  # 80% opacity (255 * 0.8)

            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    if a < opacity_threshold:
                        pixels[x, y] = (0, 0, 0, 0)  # Fully transparent pixel

            # Save the frame
            frame_file = output_folder / f"frame_{frame_number}.png"
            frame.save(frame_file, "PNG")

            if progress_callback:
                progress = frame_number / total_frames * 100
                progress_callback(progress)

        if message_callback:
            message_callback(f"All frames processed and saved to '{output_folder}'.")

    except Exception as e:
        if error_callback:
            error_callback(f"An error occurred while saving frames: {e}")



def create_preview_gif(total_frames, delays, preview_folder, progress_callback=None, message_callback=None, error_callback=None):
    """
    Creates a new GIF using the frames in the 'Frames' directory and the delay data,
    then saves it as 'preview.gif' in the 'preview' folder.
    """
    try:
        frames_folder = exe_path_fs('game_data/frames')
        output_gif_path = exe_path_fs('game_data/stamp_preview/preview.gif')


        frames = []
        frame_durations = []
        for frame_number in range(1, total_frames + 1):
            frame_path = frames_folder / f"frame_{frame_number}.png"
            if not os.path.exists(frame_path):
                if message_callback:
                    message_callback(f"Frame {frame_number} not found at {frame_path}")
                continue
            frame = Image.open(frame_path).convert('RGBA')
            frames.append(frame)
            frame_durations.append(delays[frame_number - 1])  # Duration in ms

        if not frames:
            if error_callback:
                error_callback("No frames found to create preview GIF.")
            return

        # Prepare frames for GIF with transparency
        converted_frames = []
        for frame in frames:
            # Ensure the frame has an alpha channel
            frame = frame.convert('RGBA')

            # Create a transparent background
            background = Image.new('RGBA', frame.size, (0, 0, 0, 0))  # Transparent background

            # Composite the frame onto the background
            combined = Image.alpha_composite(background, frame)

            # Convert to 'P' mode (palette) with an adaptive palette
            # The 'transparency' parameter will handle the transparent color
            combined_p = combined.convert('P', palette=Image.ADAPTIVE, colors=255)

            # Find the color index that should be transparent
            # Here, we assume that the first color in the palette is transparent
            # Alternatively, you can search for a specific color
            # For robustness, let's search for the color with alpha=0
            transparent_color = None
            for idx, color in enumerate(combined_p.getpalette()[::3]):
                r = combined_p.getpalette()[idx * 3]
                g = combined_p.getpalette()[idx * 3 + 1]
                b = combined_p.getpalette()[idx * 3 + 2]
                # Check if this color is used for transparency in the original image
                # This is a simplistic check; for complex images, more logic may be needed
                if (r, g, b) == (0, 0, 0):  # Assuming black is the transparent color
                    transparent_color = idx
                    break

            if transparent_color is None:
                # If not found, append black to the palette and set it as transparent
                combined_p.putpalette(combined_p.getpalette() + [0, 0, 0])
                transparent_color = len(combined_p.getpalette()) // 3 - 1

            # Assign the transparency index
            combined_p.info['transparency'] = transparent_color

            converted_frames.append(combined_p)

        # Save the frames as a GIF with transparency
        converted_frames[0].save(
            output_gif_path,
            save_all=True,
            append_images=converted_frames[1:],
            duration=frame_durations,
            loop=0,
            transparency=converted_frames[0].info['transparency'],
            disposal=2
        )

        if message_callback:
            message_callback(f"Preview GIF saved at: {output_gif_path}")

    except Exception as e:
        if error_callback:
            error_callback(f"An error occurred in create_preview_gif: {e}")

def create_and_clear_preview_folder(message_callback=None):
    """
    Creates and clears the 'preview' folder.
    Returns the path to the 'preview' folder.
    """
    preview_folder = exe_path_fs('game_data/stamp_preview')
    os.makedirs(preview_folder, exist_ok=True)

    # Clear 'preview' folder
    for filename in os.listdir(preview_folder):
        file_path = preview_folder / filename
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
        except Exception as e:
            if message_callback:
                message_callback(f'Failed to delete {file_path}. Reason: {e}')
    return preview_folder

def main(image_path, remove_bg, preprocess_flag, use_lab_flag, brightness_flag, resize_dim, color_key_array, process_mode, process_params, progress_callback=None, message_callback=None, error_callback=None):
    """
    Main function to process the image or GIF based on the provided parameters.
    """
    global use_lab
    use_lab = use_lab_flag
    global brightness
    brightness = brightness_flag
    try:
        if message_callback:
            message_callback("Initializing...")

        # Handle 'clip' as image_path
        if image_path == 'clip':
            try:
                img = ImageGrab.grabclipboard()
                if img is None:
                    if error_callback:
                        error_callback("No image found in clipboard.")
                    return
                elif isinstance(img, list):
                    # Clipboard contains file paths
                    image_files = [f for f in img if os.path.isfile(f)]
                    if not image_files:
                        if error_callback:
                            error_callback("No image files found in clipboard.")
                        return
                    # Use the first image file
                    image_path = image_files[0]
                    img = Image.open(image_path)
                    if message_callback:
                        message_callback(f"Image loaded from clipboard file: {image_path}")
                elif isinstance(img, Image.Image):
                    if message_callback:
                        message_callback("Image grabbed from clipboard.")
                else:
                    if error_callback:
                        error_callback("Clipboard does not contain an image or image file.")
                    return
            except ImportError:
                if error_callback:
                    error_callback("PIL.ImageGrab is not available on this system.")
                return
            except Exception as e:
                if error_callback:
                    error_callback(f"Error accessing clipboard: {e}")
                return
        else:
            try:
                img = Image.open(image_path)
            except FileNotFoundError:
                if error_callback:
                    error_callback(f"File not found: {image_path}")
                return
            except UnidentifiedImageError:
                if error_callback:
                    error_callback(f"The file '{image_path}' is not a valid image.")
                return

        # Check if image is animated
        is_multiframe = getattr(img, "is_animated", False)

        if is_multiframe:
            if message_callback:
                message_callback("Processing animated image...")
            # Save the image to a temporary path if it's from the clipboard
            if image_path == 'clip':
                temp_image_path = exe_path_fs('exe_data/temp/clipboard_image.webp')
                img.save(temp_image_path, 'WEBP')
                image_path = temp_image_path
            process_and_save_gif(image_path, resize_dim, process_mode, use_lab_flag, process_params, color_key_array, remove_bg, preprocess_flag, progress_callback, message_callback, error_callback)
        else:
            if message_callback:
                message_callback("Processing image...")
            process_and_save_image(img, resize_dim, process_mode, use_lab_flag, process_params, color_key_array, remove_bg, preprocess_flag, progress_callback, message_callback, error_callback)

        if message_callback:
            message_callback("Processing complete!")

    except Exception as e:
        if error_callback:
            error_callback(str(e))

class WorkerSignals(QObject):
    progress = Signal(float)  # For progress percentage
    message = Signal(str)     # For status messages
    error = Signal(str)

class ClickableLabel(QLabel):
    """
    A QLabel that emits a signal when clicked and allows toggling clickability.
    Only emits the signal on mouse release if the label was not dragged.
    """
    clicked = Signal()

    def __init__(self, parent=None, is_clickable=True):
        super().__init__(parent)
        self.is_clickable = is_clickable  # Initialize with default clickability
        self._drag_threshold = 3  # Reduced pixel threshold for detecting drag
        self._mouse_start_pos = None  # To track the starting position of the mouse press
        self._dragged = False  # Track if the label has been dragged

    def mousePressEvent(self, event):
        if self.is_clickable and event.button() == Qt.LeftButton:
            self._mouse_start_pos = event.position().toPoint()  # Use position() and convert to QPoint
            self._dragged = False  # Reset dragged state on mouse press
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_clickable and self._mouse_start_pos is not None:
            # Calculate the distance moved since the mouse press
            distance = (event.position().toPoint() - self._mouse_start_pos).manhattanLength()
            if distance > self._drag_threshold:
                self._dragged = True  # Mark as dragged if distance exceeds threshold
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_clickable and event.button() == Qt.LeftButton:
            # Only emit clicked if not dragged and threshold not exceeded
            if not self._dragged:
                self.clicked.emit()
        super().mouseReleaseEvent(event)


class CanvasWorker(QObject):
    """
    Worker class to handle JSON updates, monitoring, and image generation in a separate thread.
    """
    show_message = Signal(str, bool)  # (message, is_error)
    images_finished = Signal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.COLOR_MAP = {
            0: 'ffe7c5',
            1: '2a3844',
            2: 'd70b5d',
            3: '0db39e',
            4: 'f4c009',
            5: 'ff00ff',
            6: 'bac357'
        }

    @Slot(str, str)  # Receives config_path and json_path as strings
    def process_canvas(self, config_path, json_path):
        print("process_canvas called")

        try:
            # Step 1: Update JSON request
            try:
                with open(config_path, "r+") as file:
                    data = json.load(file)
                    data["walky_talky_webfish"] = "get the canvas data bozo"
                    data["walky_talky_menu"] = "nothing new!"
                    file.seek(0)
                    json.dump(data, file, indent=4)
                    file.truncate()
                print("JSON updated successfully.")
                self.show_message.emit("Canvas request sent!", False)
            except Exception as e:
                print(f"Failed to update JSON: {e}")
                print("Failed to update JSON")
                return
            # Step 2: Monitor JSON status
            timeout = time.time() + 4  # 4-second timeout
            success = False
            previous_menu_value = "nothing new!"

            while time.time() < timeout:
                time.sleep(0.5)  # Poll every 0.5 seconds
                try:
                    with open(config_path, "r") as file:
                        data = json.load(file)

                        # Track changes in "walky_talky_menu"
                        current_menu_value = data.get("walky_talky_menu", "nothing new!")
                        if current_menu_value != previous_menu_value:
                            previous_menu_value = current_menu_value

                        # Check if "walky_talky_webfish" is reset
                        if data.get("walky_talky_webfish") == "nothing new!":
                            menu_value = current_menu_value
                            if menu_value != "nothing new!":
                                if menu_value == "Canvas data exported!":
                                    success = True
                                    break
                                else:
                                    self.show_message.emit(menu_value, True)
                                    self.images_finished.emit(False)
                                    return

                except json.JSONDecodeError as json_error:
                    print(f"JSON parsing error: {json_error}")
                except FileNotFoundError as file_error:
                    print(f"File not found: {file_error}")
                except Exception as e:
                    print(f"Unexpected error reading JSON: {e}")

            # Handle success or failure after the loop
            if not success:
                self.show_message.emit("Game is probably not open.", True)
                self.images_finished.emit(False)
                return

            # Step 3: Generate Images
            print("Starting image generation...")
            self.generate_images_from_json(Path(json_path))

            # Notify the UI that image generation is complete
            self.images_finished.emit(True)
            print("Image generation complete.")

        except Exception as e:
            print(f"Exception in process_canvas: {e}")

    def generate_images_from_json(self, json_path: Path):
        """
        Processes exported canvas data JSON and generates PNG images.
        """
        output_directory = Path("game_data/game_canvises").resolve()
        output_directory.mkdir(parents=True, exist_ok=True)

        try:
            with open(json_path, "r") as file:
                canvas_data = json.load(file)

            def process_canvas(canvas_name: str, points: list):
                img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
                pixels = img.load()
                for i in range(0, len(points), 3):
                    try:
                        x, y, color_idx = points[i:i + 3]
                        if not (0 <= x < 200 and 0 <= y < 200):
                            continue
                        hex_color = self.COLOR_MAP.get(color_idx, "000000")
                        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
                        pixels[x, y] = (r, g, b, 255)
                    except Exception as e:
                        print(f"Error processing canvas '{canvas_name}': {e}")
                output_path = output_directory / f"{canvas_name.replace(' ', '_').lower()}.png"
                img.save(output_path)
                print(f"Saved image: {output_path}")

            print("Processing canvas data...")
            with ThreadPoolExecutor(max_workers=4) as executor:
                for canvas_name, points in canvas_data.items():
                    executor.submit(process_canvas, canvas_name, points)

            print("Image generation complete.")

        except Exception as e:
            print(f"Error generating images: {e}")


class ImageProcessingThread(threading.Thread):
    def __init__(self, params, signals):
        super().__init__()
        self.params = params
        self.signals = signals
        self.executor = ThreadPoolExecutor(max_workers=6)  # Adjust based on your system's capability

    def run(self):
        try:
            # Submit the main function to the thread pool
            future = self.executor.submit(
                main,
                image_path=self.params['image_path'],
                remove_bg=self.params['remove_bg'],
                preprocess_flag=self.params['preprocess_flag'],
                use_lab_flag=self.params['use_lab'],
                brightness_flag=self.params['brightness'],
                resize_dim=self.params['resize_dim'],
                color_key_array=self.params['color_key_array'],
                process_mode=self.params['process_mode'],
                process_params=self.params['process_params'],
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                error_callback=self.signals.error.emit
            )

            # Wait for the task to complete and capture exceptions if any
            result = future.result()
            self.signals.message.emit("Processing finished")
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.executor.shutdown()


class MainWindow(QMainWindow):
    start_process_canvas = Signal(str, str)
    def __init__(self):
        super().__init__()
        self.back_button = None
        self.delete_mode = False
        self.last_message_displayed = None
        self.connected = False
        self.window_titles = [
            "I <3 PEANITS",
            "are you kidding me?",
            "wOrks on My machine",
            "the hunt for purple chalk",
            "video game lover",
            "Color?? i hardly know 'er",
            "yiff poster 9000",
            "Pupple Puppyy wuz here",
            "u,mm, Haiiii X3",
            "neeeddd.. moooree.. .adralll ,.,",
            "animal people",
            "aaaaand its all over my screen",
            "if ive gone missin  ive gon fishn!",
            "the world is SPINNING, SPINNING!",
            "Now with ai!",
            "Full of spagetti",
            "i ated purple chalk!",
            "made by ChatGBT in just 8 minutes",
            "Fuck my chungus life",
            "Whaaatt? you dont have qhd???"
        ]
        self.setWindowTitle(random.choice(self.window_titles))
        self.setFixedSize(700, 768)
        self.move_to_bottom_right()
        self._is_dragging = False
        self._drag_position = QPoint()
        # Initialize variables
        self.processing = False
        self.image_path = None
        self.image = None
        self.current_temp_file = None
        self.is_gif = False
        self.canpaste = True
        self.current_image_pixmap = None
        self.parameter_widgets = {}
        self.new_color = None  # Single color 5
        self.autocolor = True
        self.default_color_key_array = [
            {'number': 0, 'hex': 'ffe7c5', 'boost': 1.2, 'threshold': 20},
            {'number': 1, 'hex': '2a3844', 'boost': 1.2, 'threshold': 20},
            {'number': 2, 'hex': 'd70b5d', 'boost': 1.2, 'threshold': 20},
            {'number': 3, 'hex': '0db39e', 'boost': 1.2, 'threshold': 20},
            {'number': 4, 'hex': 'f4c009', 'boost': 1.2, 'threshold': 20},
            {'number': 6, 'hex': 'bac357', 'boost': 1.2, 'threshold': 20},
        ]
        self.button_stylesheet = """
            QPushButton {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #7b1fa2, stop:1 #9c27b0);
                color: white;
                border-radius: 15px;  /* Rounded corners */
                font-family: 'Comic Sans MS', sans-serif;
                font-size: 20px;
                font-weight: bold;
                padding: 15px 30px;
            }
            QPushButton:hover {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #9c27b0, stop:1 #d81b60);
            }
            QPushButton:pressed {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6a0080, stop:1 #880e4f);
            }
        """

        # Setup UI
        self.setup_ui()
        self.init_worker()
        self.bring_to_front()

    def init_worker(self):
        # Initialize worker and thread
        self.worker_thread = QThread()
        self.worker = CanvasWorker()
        self.worker.moveToThread(self.worker_thread)

        # Connect signals and slots
        self.start_process_canvas.connect(self.worker.process_canvas)
        self.worker.show_message.connect(self.show_floating_message)
        self.worker.images_finished.connect(self.on_images_finished)
        
    def move_to_bottom_right(self):
        """
        Moves the window to the bottom-right corner of the screen with a 26-pixel offset.
        """
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        x = screen_geometry.x() + screen_geometry.width() - self.width() - 26  # 26px offset from the right
        y = screen_geometry.y() + screen_geometry.height() - self.height() - 56  # 26px offset from the bottom
        self.move(x, y)


    def bring_to_front(self):
        """Brings the window to the front without disabling the close button."""
        # Preserve the existing flags while ensuring 'WindowStaysOnTopHint' is added temporarily
        original_flags = self.windowFlags()
        self.setWindowFlags(original_flags | Qt.WindowStaysOnTopHint)
        self.show()
        self.activateWindow()
        self.raise_()
        # Restore the original flags
        self.setWindowFlags(original_flags)
        self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Determine the widget under the cursor
            child = self.childAt(event.position().toPoint())
            # If no child widget is under the cursor or it's not an interactive widget, initiate dragging
            if child is None or not isinstance(child, (QPushButton,)):
                self._is_dragging = True
                self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
            else:
                self._is_dragging = False
                
    def mouseReleaseEvent(self, event):
        self._is_dragging = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._is_dragging:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def setup_ui(self):
        """
        Sets up the main UI with the stacked widget, menus, and persistent signature.
        """
        # Apply dark-themed stylesheet with purple accents
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1a33; /* Very dark purple */
                color: #ffffff;
                font-family: 'Comic Sans MS', sans-serif; /* Use Comic Sans */
                font-weight: bold; /* Bold text */
                font-size: 16px; /* Slightly larger */
            }
            QPushButton {
                background-color: #7b1fa2;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
                font-size: 16px; /* Slightly larger */
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
            }
            QPushButton:hover {
                background-color: #9c27b0;
            }
            QPushButton:disabled {
                background-color: #4a148c;
            }
            QLabel {
                font-size: 16px; /* Slightly larger */
                color: #ffffff;
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
            }
            QCheckBox {
                font-size: 16px; /* Slightly larger */
                color: #ffffff;
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
            }
            QSlider::groove:horizontal {
                height: 8px;
                background: #7b1fa2;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #ba68c8;
                border: 1px solid #ffffff;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QComboBox, QSpinBox, QLineEdit {
                font-size: 16px; /* Slightly larger */
                padding: 5px;
                border: 1px solid #7b1fa2;
                border-radius: 5px;
                background-color: #424242;
                color: #ffffff;
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
            }
            QProgressBar {
                height: 15px;
                border: 1px solid #7b1fa2;
                border-radius: 7px;
                text-align: center;
                background-color: #424242;
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
                font-size: 16px; /* Slightly larger */
            }
            QProgressBar::chunk {
                background-color: #ba68c8;
                width: 1px;
            }
            QGroupBox {
                border: 1px solid #7b1fa2;
                border-radius: 5px;
                margin-top: 10px;
                color: #ffffff;
                font-family: 'Comic Sans MS', sans-serif; /* Ensure Comic Sans */
                font-weight: bold; /* Bold text */
                font-size: 16px; /* Slightly larger */
            }
        """)


        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout for the entire application
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # Stacked widget for switching between menus
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

        # Initial menu
        self.setup_initial_menu()



    def keyPressEvent(self, event):
        """
        Override keyPressEvent to detect Ctrl+V (Paste).
        """
        if event.key() == Qt.Key_V and (event.modifiers() & Qt.ControlModifier) and self.canpaste:
            self.open_image_from_clipboard(True)
        else:
            super().keyPressEvent(event)

    def setup_initial_menu(self):
        """
        Sets up the initial menu with enhanced buttons and a random image 
        from the 'menu_pics' directory as a background image.
        Adds Save Current and Load buttons below the primary ones.
        """


        # Initialize the main widget
        initial_widget = QWidget()
        initial_layout = QVBoxLayout()
        initial_layout.setContentsMargins(0, 0, 0, 0)  # Remove all margins
        initial_layout.setSpacing(10)  # Minimal spacing between elements
        initial_layout.setAlignment(Qt.AlignCenter | Qt.AlignTop)
        initial_widget.setLayout(initial_layout)

        # -------------------------
        # Top Layout: Always on Top Checkbox
        # -------------------------
        top_layout = QHBoxLayout()
        top_layout.setSpacing(0)  # No spacing needed


        # Pin button container
        pin_container = QWidget()
        pin_layout = QHBoxLayout()
        pin_layout.setContentsMargins(0, 0, 0, 0)  # No margins for precise placement
        pin_layout.setAlignment(Qt.AlignRight | Qt.AlignTop)
        pin_container.setLayout(pin_layout)

        # Add the pin button
        self.always_on_top_checkbox = QCheckBox()
        self.always_on_top_checkbox.setFixedSize(80, 80)  # Set size to 100x100
        self.always_on_top_checkbox.setStyleSheet(f"""
            QCheckBox {{
                background: transparent;
                border: none;
            }}
            QCheckBox::indicator {{
                width: 80px;
                height: 80px;
                image: url({exe_path_stylesheet("exe_data/font_stuff/tack.svg")});
            }}
            QCheckBox::indicator:checked {{
                image: url({exe_path_stylesheet("exe_data/font_stuff/tack_down.svg")});
            }}
            QCheckBox::indicator:hover {{
                image: url({exe_path_stylesheet("exe_data/font_stuff/tack_hover.svg")});
            }}
        """)
        self.always_on_top_checkbox.setChecked(False)
        
        self.always_on_top_checkbox.toggled.connect(self.toggle_always_on_top)
        pin_layout.addWidget(self.always_on_top_checkbox)

        # Add the pin_container to the top_layout
        top_layout.addWidget(pin_container)

        # Add the top_layout to the initial_layout
        initial_layout.addLayout(top_layout)

        # -------------------------
        # Background Container: Image and Buttons
        # -------------------------
        background_container = QWidget()
        background_layout = QVBoxLayout()
        background_layout.setContentsMargins(0, 0, 0, 0)  # Minimal top margin
        background_layout.setSpacing(0)
        background_layout.setAlignment(Qt.AlignCenter | Qt.AlignTop)  # Center contents
        background_container.setLayout(background_layout)
        self.my_spacer = QWidget()
        # Create a ClickableLabel to hold the background image
        self.background_label = ClickableLabel()
        self.background_label.setFixedSize(680, 460)
        self.background_label.setPixmap(self.load_and_display_random_image())
        self.background_label.setAlignment(Qt.AlignCenter)

        self.background_label.setScaledContents(False)  # Prevent automatic scaling
        background_layout.addWidget(self.background_label, alignment=Qt.AlignCenter)
        spacer = QSpacerItem(0, 20, QSizePolicy.Minimum, QSizePolicy.Expanding)
        background_layout.addItem(spacer)

        # Optionally, connect the clicked signal
        # Example: self.background_label.clicked.connect(self.handle_background_click)

        # -------------------------
        # Button Container: Stamp Buttons and Control Buttons
        # -------------------------
        button_container = QWidget()
        button_layout = QVBoxLayout()
        button_layout.setSpacing(20)  # Space between button rows
        button_layout.setAlignment(Qt.AlignTop)  # Align buttons to the top
        button_container.setLayout(button_layout)

        # Button Stylesheet
        button_stylesheet = """
            QPushButton {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #7b1fa2, stop:1 #9c27b0);
                color: white;
                border-radius: 15px;  /* Rounded corners */
                font-family: 'Comic Sans MS', sans-serif;
                font-size: 20px;
                font-weight: bold;
                padding: 15px 30px;
            }
            QPushButton:hover {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #9c27b0, stop:1 #d81b60);
            }
            QPushButton:pressed {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6a0080, stop:1 #880e4f);
            }
        """

        # First row of buttons: "Stamp from Files" and "Stamp from Clipboard"
        top_button_layout = QHBoxLayout()
        top_button_layout.setSpacing(20)
        top_button_layout.setAlignment(Qt.AlignCenter)

        self.new_image_files_button = QPushButton("Stamp from Files")
        self.new_image_files_button.setStyleSheet(button_stylesheet)
        self.new_image_files_button.setMinimumSize(200, 60)
        self.new_image_files_button.clicked.connect(self.open_image_from_files)
        top_button_layout.addWidget(self.new_image_files_button)

        self.new_image_clipboard_button = QPushButton("Save In-Game Art")
        self.new_image_clipboard_button.setStyleSheet(button_stylesheet)
        self.new_image_clipboard_button.setMinimumSize(200, 60)
        self.new_image_clipboard_button.clicked.connect(self.request_and_monitor_canvas)
        top_button_layout.addWidget(self.new_image_clipboard_button)

        button_layout.addLayout(top_button_layout)

        # Second row of buttons: "Save Menu" and "Exit"
        bottom_button_layout = QHBoxLayout()
        bottom_button_layout.setSpacing(20)
        bottom_button_layout.setAlignment(Qt.AlignCenter)

        self.save_button = QPushButton("Save Menu")
        self.save_button.setStyleSheet(button_stylesheet)
        self.save_button.setMinimumSize(160, 60)
        self.save_button.clicked.connect(self.show_save_menu)
        bottom_button_layout.addWidget(self.save_button)

        self.exit_button = QPushButton("Mod Options / Info")
        self.exit_button.setStyleSheet(button_stylesheet)
        self.exit_button.setMinimumSize(240, 60)
        #self.exit_button.clicked.connect(self.request_and_monitor_canvas)
        bottom_button_layout.addWidget(self.exit_button)

        button_layout.addLayout(bottom_button_layout)

        # Add the button_container to the background_layout
        background_layout.addWidget(button_container, alignment=Qt.AlignCenter)

        # -------------------------
        # Add the background_container to the initial_layout
        # -------------------------
        initial_layout.addWidget(background_container, alignment=Qt.AlignCenter)

                # -------------------------
        # Add the initial widget to the stacked widget
        # -------------------------
        self.stacked_widget.addWidget(initial_widget)



    def request_and_monitor_canvas(self):
        """
        Initiates the canvas request and monitoring process.
        """
        if self.processing:
            self.show_floating_message("Request already sent")
            return
        
        self.worker_thread.start()
        config_path = get_config_path()  # Define this function appropriately
        json_path = exe_path_fs("game_data/game_canvises/game_canvises.json")  # Define this function

        if not os.path.exists(config_path):
            self.show_floating_message("Config path does not exist.", True)
            return

        if not os.path.exists(json_path):
            self.show_floating_message("JSON path does not exist.", True)
            return

        # Set the processing flag
        self.processing = True

        # Emit signal to start processing in the worker thread
        self.start_process_canvas.emit(str(config_path), str(json_path))
        print("Emitted start_process_canvas signal.")



    @Slot(bool)
    def on_images_finished(self, success):
        """
        Callback when image generation is complete.
        """
        self.worker_thread.quit() 
        self.worker_thread.wait() 
        self.processing = False
        if success:
            print("Image generation completed successfully!")




    def toggle_always_on_top(self, checked):
        """
        Toggles the window's 'Always on Top' property.
        """
        if checked:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.show_floating_message("always on top: ON", True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.show_floating_message("always on top: OFF", True)
        self.show()

    def validate_image(self, entry):
        """
        Validates that the image exists and can be loaded by QPixmap.

        Args:
            entry (dict): Dictionary containing image information.

        Returns:
            bool: True if valid, False otherwise.
        """
        file_path = entry['path']
        if os.path.exists(file_path):
            try:
                pixmap = QPixmap(file_path)
                if not pixmap.isNull():
                    return True
            except Exception as e:
                print(f"Error loading image with QPixmap: {e}")
        return False

    def get_valid_image(self, combined_list, max_attempts=10):
        """
        Selects a random valid image from the combined list by trying up to max_attempts times.

        Args:
            combined_list (list): List of image entries.
            max_attempts (int): Maximum number of attempts to find a valid image.

        Returns:
            dict or None: Selected image entry or None if no valid images are found.
        """
        attempts = 0
        temp_list = combined_list.copy()
        while attempts < max_attempts and temp_list:
            selected = random.choice(temp_list)
            if self.validate_image(selected):
                return selected
            else:
                temp_list.remove(selected)
                attempts += 1
        return None
        
    def load_and_display_random_image(self):
        """
        Loads a random, non-animated image from either the menu_pics directory or the saved_stamps.json.
        Sets up click handlers based on the image source.
        Displays the image within the provided layout.
        """
        self.reset_movie()
        # 1. Gather images from menu_pics_dir
        menu_pics_dir = exe_path_stylesheet("exe_data/menu_pics")
        if not os.path.exists(menu_pics_dir):
            QMessageBox.warning(self, "Error", f"Menu pictures directory not found: {menu_pics_dir}")
            return

        # List all image files in the directory with valid extensions, excluding .gif
        image_files = [
            f for f in os.listdir(menu_pics_dir)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp'))
        ]
        # Normalize paths to use forward slashes
        menu_pics = [{'type': 'menu_pic', 'path': os.path.join(menu_pics_dir, f).replace("\\", "/")} for f in image_files]

        # 2. Gather images from saved_stamps.json
        appdata_dir = get_appdata_dir()
        saved_stamps_json_path = appdata_dir / "saved_stamps.json"
        saved_stamps_dir = appdata_dir / "saved_stamps"
        saved_stamp_entries = []

        if saved_stamps_json_path.exists():
            try:
                with open(saved_stamps_json_path, 'r') as f:
                    saved_stamps = json.load(f)

                for hash_key, value in saved_stamps.items():
                    if not value.get("is_gif", False):  # Skip animated GIFs
                        preview_full_path = (saved_stamps_dir / hash_key / "preview.webp").as_posix()
                        saved_stamp_entries.append({
                            'type': 'saved_stamp',
                            'hash': hash_key,
                            'path': preview_full_path
                        })
            except Exception as e:
                print(f"Failed to load saved stamps: {e}")
                # Continue with empty saved_stamp_entries

        # 3. Combine all images
        combined_images = menu_pics + saved_stamp_entries

        if not combined_images:
            QMessageBox.warning(self, "Error", "No images available to select.")
            return

        # 4. Select a valid, non-animated image with limited attempts
        selected_image_entry = self.get_valid_image(combined_images)

        if not selected_image_entry:
            QMessageBox.warning(self, "Error", "No valid, non-animated images could be loaded from the sources.")
            return

        # 5. Load and display the selected image
        pixmap = QPixmap(selected_image_entry['path'])
        if pixmap.isNull():
            QMessageBox.warning(self, "Error", f"Failed to load image: {selected_image_entry['path']}")
            return

        # Resize the image while maintaining aspect ratio
        if pixmap.width() > 680 or pixmap.height() > 460:
            transformation_mode = Qt.SmoothTransformation  # Downscaling
        else:
            transformation_mode = Qt.FastTransformation  # Upscaling

        scaled_pixmap = pixmap.scaled(
            680, 460,  # Max dimensions
            Qt.KeepAspectRatio,
            transformation_mode
        )


        # Apply transparency
        transparent_pixmap = QPixmap(scaled_pixmap.size())
        transparent_pixmap.fill(Qt.transparent)

        painter = QPainter(transparent_pixmap)
        painter.setOpacity(0.9)
        painter.drawPixmap(0, 0, scaled_pixmap)
        painter.end()

        # Disconnect any previously connected signals
        if self.connected:
            self.background_label.clicked.disconnect()

        # Connect the click event based on the image source
        if selected_image_entry['type'] == 'menu_pic':
            self.background_label.clicked.connect(lambda: self.open_image_from_menu(selected_image_entry['path']))
        elif selected_image_entry['type'] == 'saved_stamp':
            self.background_label.clicked.connect(lambda: self.load_thumbnail(selected_image_entry['hash']))

        self.connected = True
        return transparent_pixmap
        
    def display_new_stamp(self):
        self.reset_movie()
        # Check and load the appropriate file
        preview_png_path = exe_path_fs('game_data/stamp_preview/preview.png')
        preview_gif_path = exe_path_fs('game_data/stamp_preview/preview.gif')

        if self.connected:
            self.background_label.clicked.disconnect()
            self.connected = False

        if Path(preview_png_path).exists():
            # Load the PNG
            pixmap = QPixmap(str(preview_png_path))  # Convert Path to string

            # Resize the pixmap while maintaining the aspect ratio
            transformation_mode = Qt.FastTransformation  # Use hard edges
            scaled_pixmap = pixmap.scaled(
                680, 460,  # Max dimensions
                Qt.KeepAspectRatio,
                transformation_mode
            )

            # Update the label with the resized image
            self.background_label.clear()  # Clear any existing content
            self.background_label.setPixmap(scaled_pixmap)

        elif Path(preview_gif_path).exists():
            try:
                with Image.open(preview_gif_path) as gif:
                    # Extract all frames from the GIF
                    frames = []
                    durations = []
                    for frame in ImageSequence.Iterator(gif):
                        # Resize each frame with NEAREST interpolation
                        frame = frame.convert("RGBA")
                        scale_factor = min(680 / frame.width, 460 / frame.height)
                        new_size = (int(frame.width * scale_factor), int(frame.height * scale_factor))
                        resized_frame = frame.resize(new_size, Image.NEAREST)

                        # Convert to QImage for QPixmap
                        data = resized_frame.tobytes("raw", "RGBA")
                        qimage = QImage(data, resized_frame.width, resized_frame.height, QImage.Format_RGBA8888)
                        pixmap = QPixmap.fromImage(qimage)

                        # Store frame and duration
                        frames.append(pixmap)
                        durations.append(frame.info.get("duration", 100))  # Default to 100ms if no duration

                    if frames:
                        # Animate the frames using a QTimer
                        self.current_frame = 0
                        self.timer = QTimer(self)
                        self.timer.timeout.connect(lambda: self.update_gif_frame(frames))
                        self.timer.start(durations[0])  # Start with the first frame's duration
                        self.gif_frames = frames
                        self.gif_durations = durations
            except Exception as e:
                logging.error(f"Error processing GIF: {e}")

        else:
            # Handle the case where neither file exists
            logging.error("No valid image or GIF found in the stamp_preview directory.")

    def update_gif_frame(self, frames):
        # Update QLabel with the current frame
        self.background_label.setPixmap(self.gif_frames[self.current_frame])

        # Increment the frame index
        self.current_frame = (self.current_frame + 1) % len(self.gif_frames)

        # Update timer interval for the next frame
        next_duration = self.gif_durations[self.current_frame]
        self.timer.start(next_duration)


    def reset_movie(self):
        # Stop the movie if it is playing
        if hasattr(self, 'movie') and self.movie is not None:
            self.movie.stop()

        # Stop the timer if using manual animation
        if hasattr(self, 'timer') and self.timer is not None:
            self.timer.stop()
            self.timer = None

        # Clear the QLabel
        self.background_label.clear()

        # Reset related attributes
        self.movie = None
        self.gif_frames = None
        self.gif_durations = None
        self.current_frame = None

    def setup_secondary_menu(self):
        """
        Sets up the secondary menu with the corrected layout:
        - Image container on the left without any black borders around the image.
        - Checkboxes and sliders to the right of the image box in a vertical column.
        - Process button and accompanying elements fixed at the bottom.
        """

        # Secondary widget
        self.secondary_widget = QWidget()
        secondary_layout = QVBoxLayout()  # Main vertical layout
        secondary_layout.setContentsMargins(10, 10, 10, 10)
        self.secondary_widget.setLayout(secondary_layout)

        # Top horizontal layout for image and checkboxes
        top_layout = QHBoxLayout()
# Back button with small black border
        # Image container without background
        image_container = QFrame()
        image_container.setStyleSheet("background-color: transparent;")  # Remove background
        image_container.setFixedSize(420, 300)  # Frame is the same size as the image

        # Stack layout for image and back button
        image_layout = QStackedLayout()
        image_container.setLayout(image_layout)

        # Image label
        self.image_label = QLabel("Whoops Sorry haha")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: transparent; border: none;")  # Ensure no black border
        self.image_label.setFixedSize(420, 300)  # Exact size for the image
        image_layout.addWidget(self.image_label)

        # Back button with small black border
        self.back_button = QPushButton(self)
        self.back_button.setStyleSheet(f"""
            QPushButton {{
                border: none; 
                background-color: transparent;
                image: url({exe_path_stylesheet('exe_data/font_stuff/home.svg')});
            }}
            QPushButton:hover {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/home_hover.svg')});
            }}    
            QPushButton:pressed {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/home_hover.svg')});
            }}
        """)

        self.back_button.setFixedSize(60, 60)  # Ensure consistent size
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.clicked.connect(self.go_to_initial_menu)
        self.back_button.move(-5, -9)
        self.back_button.show()
        self.back_button.raise_()
        image_layout.addWidget(self.back_button)
        # Refresh button 60px to the right of the back button
        self.refresh_button = QPushButton(self)
        self.refresh_button.setStyleSheet(f"""
            QPushButton {{
                border: none; /* Remove any borders */
                background-color: transparent; 
                image: url({exe_path_stylesheet('exe_data/font_stuff/refresh.svg')});
            }}
            QPushButton:hover {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/refresh_hover.svg')});
            }}    
            QPushButton:pressed {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/refresh_hover.svg')});
            }}       
        """)
        self.refresh_button.setFixedSize(60, 60)  # Ensure consistent size
        self.refresh_button.setCursor(Qt.PointingHandCursor)
        self.refresh_button.clicked.connect(self.reset_color_options)
        self.refresh_button.move(self.back_button.x() + 50, self.back_button.y())  # Positioned 60px to the right
        self.refresh_button.show()
        self.refresh_button.raise_()
        image_layout.addWidget(self.refresh_button)
        # Add the image container to the layout
        top_layout.addWidget(image_container)

                # Ring-style frame to wrap all options
        # Ring-style frame to wrap all options
        ring_frame = QFrame()
        ring_frame.setStyleSheet("""
            QFrame {
                border: 3px solid #7b1fa2; /* Purple border */
                border-radius: 15px;      /* Rounded corners */
                padding: 5px;             /* Reduced inner padding */
                margin: 0px;              /* Outer margin */
                background-color: transparent;
            }
        """)

        # Layout inside the ring frame
        ring_layout = QVBoxLayout()
        ring_layout.setContentsMargins(5, 5, 5, 5)  # Optional: Reduce if needed
        ring_layout.setSpacing(10)  # Reduced spacing between elements
        ring_frame.setLayout(ring_layout)

        # Title for the preprocess options
        title_label = QLabel("Processing Options")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 20px;
                font-weight: bold;
                margin: 0px;  /* No extra spacing */
                border: none; /* No border */
                background: none; /* Transparent background */
            }
        """)
        ring_layout.addWidget(title_label)

        # Preprocess Image checkbox
        self.preprocess_checkbox = QCheckBox("Preprocess Image")
        self.preprocess_checkbox.setChecked(True)
        self.preprocess_checkbox.setStyleSheet(f"""
            QCheckBox {{
                font-size: 16px;
                color: white;
                border: none; /* No border */
                background: none; /* Transparent background */
                margin: 0px; /* No margin */
            }}
            QCheckBox::indicator {{
                width: 24px;
                height: 24px;
            }}
            QCheckBox::indicator:unchecked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/uncheck.svg')});
            }}
            QCheckBox::indicator:checked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/check.svg')});
            }}
        """)
        ring_layout.addWidget(self.preprocess_checkbox)

        # AI Background Removal Checkbox
        self.bg_removal_checkbox = QCheckBox("Background Removal")
        self.bg_removal_checkbox.setStyleSheet(f"""
            QCheckBox {{
                font-size: 16px;
                color: white;
                border: none; /* No border */
                background: none; /* Transparent background */
                margin: 0px; /* No margin */
            }}
            QCheckBox::indicator {{
                width: 24px;
                height: 24px;
            }}
            QCheckBox::indicator:unchecked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/uncheck.svg')});
            }}
            QCheckBox::indicator:checked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/check.svg')});
            }}
        """)
        ring_layout.addWidget(self.bg_removal_checkbox)

        # Custom Filter Checkbox
        self.lab_color_checkbox = QCheckBox("Use LAB Colors")
        self.lab_color_checkbox.setStyleSheet(f"""
            QCheckBox {{
                font-size: 16px;
                color: white;
                border: none; /* No border */
                background: none; /* Transparent background */
                margin: 0px; /* No margin */
            }}
            QCheckBox::indicator {{
                width: 24px;
                height: 24px;
            }}
            QCheckBox::indicator:unchecked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/uncheck.svg')});
            }}
            QCheckBox::indicator:checked {{
                image: url({exe_path_stylesheet('exe_data/font_stuff/check.svg')});
            }}
        """)
        ring_layout.addWidget(self.lab_color_checkbox)

        # Brightness Layout
        brightness_layout = QHBoxLayout()
        brightness_layout.setContentsMargins(0, 0, 0, 0)
        brightness_layout.setSpacing(2)  # Reduced spacing
        self.brightness_label = QLabel("Brightness")
        self.brightness_label.setAlignment(Qt.AlignCenter)
        self.brightness_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px; /* Larger text */
                font-weight: bold;
                border: none; /* No ring */
                margin-bottom: 2px; /* Reduced margin below the label */
            }
        """)
        brightness_layout.addWidget(self.brightness_label)

        # Brightness Slider
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(50)
        self.brightness_slider.setTickInterval(1)
        self.brightness_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #7b1fa2;
                border-radius: 3px;
                margin: 0px; /* Remove any default margins */
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #7b1fa2;
                width: 14px;
                margin: -5px 0; /* Adjust handle position */
                border-radius: 7px;
            }
        """)
        brightness_layout.addWidget(self.brightness_slider)

        # Add the brightness layout to the main ring layout
        ring_layout.addLayout(brightness_layout)

        # Processing Method title
        processing_label = QLabel("Processing Method:")
        processing_label.setAlignment(Qt.AlignTop | Qt.AlignCenter) 
        processing_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 17px;
                font-weight: bold;
                border: none;
                margin-bottom: 2px; /* Minimal bottom margin */
                padding-bottom: 0px; /* Minimal bottom padding */
            }
        """)
        ring_layout.addWidget(processing_label)

        # Retrieve processing methods from imageprocess
        self.processing_methods = [
            {"name": name, "description": getattr(func, "description", "")}
            for name, func in processing_method_registry.items()
        ]
        self.processing_combobox = QComboBox()
        self.processing_combobox.addItems([method["name"] for method in self.processing_methods])
        self.processing_combobox.setStyleSheet("""
            QComboBox {
                background-color: #7b1fa2;
                color: white;
                border-radius: 5px;
                font-family: 'Comic Sans MS', sans-serif;
                font-size: 16px;
                font-weight: bold;
                padding: 5px;
                margin: 0px; /* No margin */
            }
            QComboBox:hover {
                background-color: #9c27b0;
            }
            QComboBox::drop-down {
                border-radius: 0px;
            }
            QComboBox QAbstractItemView {
                background-color: #7b1fa2;
                color: white;
                selection-background-color: #9c27b0;
            }
        """)
        ring_layout.addWidget(self.processing_combobox)
        self.processing_combobox.currentTextChanged.connect(self.processing_method_changed)
    
        # Final adjustments
        ring_frame.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        ring_frame.setMaximumSize(256, 1000)
        # Add the ring frame to the top layout (to the right of the image)
        top_layout.addWidget(ring_frame, alignment=Qt.AlignBottom)



        # Add the top layout (image + checkboxes) to the main layout
        secondary_layout.addLayout(top_layout)

        # Resize options
        resize_layout = QHBoxLayout()
        resize_layout.setSpacing(10)
        secondary_layout.addLayout(resize_layout)

        resize_label = QLabel("Resize (max dim):")
        resize_layout.addWidget(resize_label, alignment=Qt.AlignTop)

        self.resize_slider = QSlider(Qt.Horizontal)
        self.resize_slider.setRange(6, 400)
        self.resize_slider.setValue(128)
        self.resize_slider.setTickInterval(10)
        self.resize_slider.setTickPosition(QSlider.TicksBelow)
        resize_layout.addWidget(self.resize_slider, alignment=Qt.AlignTop)

        self.resize_value_label = QLabel("128")
        resize_layout.addWidget(self.resize_value_label, alignment=Qt.AlignTop)

        self.resize_slider.valueChanged.connect(self.resize_slider_changed)

        self.method_options_layout = QFormLayout()
        # Wrapper widget to ensure top alignment for the method options layout
        method_options_widget = QWidget()
        method_options_widget_layout = QVBoxLayout()
        method_options_widget_layout.setAlignment(Qt.AlignTop)  # Align top
        method_options_widget_layout.setContentsMargins(0, 0, 0, 0)  # Remove extra margins
        method_options_widget.setLayout(method_options_widget_layout)

        # Add the method options layout to the wrapper layout
        method_options_widget_layout.addLayout(self.method_options_layout)

        # Add the wrapper widget to the secondary layout
        secondary_layout.addWidget(method_options_widget)


        # Initialize parameter widgets
        self.parameter_widgets = {}

        # Color options
        self.setup_color_options_ui(secondary_layout)

        # Initially populate method options
        self.processing_method_changed('self.processing_combobox.currentText()')

        # Action layout for process button, status label, and progress bar
        self.action_layout = QStackedWidget()

        # Process button
        self.process_button = QPushButton("Yeaaah Process it!")
        self.process_button.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #7b1fa2, stop:1 #9c27b0);
                color: white;
                border-radius: 15px;  /* Rounded corners */
                font-family: 'Comic Sans MS', sans-serif;
                font-size: 24px;
                font-weight: bold;
                padding: 15px 30px;
            }
            QPushButton:hover {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #9c27b0, stop:1 #d81b60);
            }
            QPushButton:pressed {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6a0080, stop:1 #880e4f);
            }
        """)
        self.process_button.setMinimumHeight(60)
        self.process_button.setCursor(Qt.PointingHandCursor)
        self.process_button.clicked.connect(self.process_image)
        self.action_layout.addWidget(self.process_button)

        # Status layout with status label and progress bar
        status_widget = QWidget()
        status_layout = QVBoxLayout()
        status_widget.setLayout(status_layout)

        self.status_label = QLabel("Status: Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setVisible(False)
        status_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(20)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        status_layout.addWidget(self.progress_bar)

        self.action_layout.addWidget(status_widget)

        # Add the action layout and force alignment at the bottom
        process_widget = QWidget()
        process_layout = QVBoxLayout()
        process_layout.addWidget(self.action_layout)
        process_widget.setLayout(process_layout)
        process_widget.setFixedHeight(80)
        secondary_layout.addWidget(process_widget, alignment=Qt.AlignBottom)

        # Add the secondary widget to the stacked widget
        self.stacked_widget.addWidget(self.secondary_widget)


    def setup_result_menu(self):
        """
        Sets up the result menu with the processed image display and styled buttons.
        Ensures the displayed GIF or image maintains its aspect ratio and is not stretched or glued to edges.
        """
        # Result widget
        self.result_widget = QWidget()
        result_layout = QVBoxLayout()
        result_layout.setAlignment(Qt.AlignCenter)
        self.result_widget.setLayout(result_layout)
        self.current_label = QLabel("Current Stamp:")
        self.current_label.setAlignment(Qt.AlignCenter)
        self.current_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 64px; /* Larger text */
                font-weight: bold;
                border: none; /* No ring */
                margin-bottom: 2px; /* Reduced margin below the label */
            }
        """)
        result_layout.addWidget(self.current_label)


        self.result_image_label = QLabel()
        self.result_image_label.setAlignment(Qt.AlignCenter)
        self.result_image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        result_layout.addWidget(self.result_image_label)




        # Buttons container
        button_container = QWidget()
        button_layout = QHBoxLayout()
        button_layout.setSpacing(20)
        button_layout.setAlignment(Qt.AlignCenter)
        button_container.setLayout(button_layout)

        # Styling for buttons (reused from the initial menu)
        button_stylesheet = """
            QPushButton {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #7b1fa2, stop:1 #9c27b0);
                color: white;
                border-radius: 15px;  /* Rounded corners */
                font-family: 'Comic Sans MS', sans-serif;
                font-size: 30px;  /* Corrected font size syntax */
                font-weight: bold;
                padding: 15px 30px;
                min-height: 50px;
            }
            QPushButton:hover {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #9c27b0, stop:1 #d81b60);
            }
            QPushButton:pressed {
                background-color: qlineargradient(
                    spread:pad, x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6a0080, stop:1 #880e4f);
            }
        """

        # "Maybe not..." button
        self.maybe_not_button = QPushButton("Back to Options")
        self.maybe_not_button.setStyleSheet(button_stylesheet)
        self.maybe_not_button.setMinimumSize(240, 60)
        self.maybe_not_button.clicked.connect(self.retry_processing)
        button_layout.addWidget(self.maybe_not_button)

        self.save_button = QPushButton("Save")
        self.save_button.setStyleSheet(button_stylesheet)
        self.save_button.setMinimumSize(100, 60)
        # Placeholder action for Save button
        self.save_button.clicked.connect(self.save_current)
        button_layout.addWidget(self.save_button)

        # "Awrooo!" button
        self.awrooo_button = QPushButton("Home")
        self.awrooo_button.setStyleSheet(button_stylesheet)
        self.awrooo_button.setMinimumSize(120, 60)
        self.awrooo_button.clicked.connect(lambda: self.go_to_initial_menu(True))
        button_layout.addWidget(self.awrooo_button)


        # Add the button container to the result layout
        result_layout.addWidget(button_container)
        # Add result widget to stacked widget
        self.stacked_widget.addWidget(self.result_widget)
        self.bring_to_front


    def setup_save_menu(self):
        """
        Sets up the Save Menu with a top button layout and a dynamic, scrollable thumbnail grid.
        """
        self.save_menu_widget = QWidget()
        self.save_menu_layout = QVBoxLayout()
        self.save_menu_layout.setContentsMargins(0, 0, 0, 0)
        self.save_menu_layout.setSpacing(0)
        self.save_menu_widget.setLayout(self.save_menu_layout)

        # Set background color for the entire menu
        self.save_menu_widget.setStyleSheet("background-color: #1E1A33;")

        # Button Container
        button_container = QWidget()
        button_layout = QHBoxLayout()  # Use horizontal layout for a single row
        button_layout.setContentsMargins(16, 16, 16, 16)
        button_layout.setSpacing(16)  # Add spacing between buttons
        button_container.setLayout(button_layout)
        self.save_menu_layout.addWidget(button_container, alignment=Qt.AlignTop)

        # Add Buttons
        buttons = [
            {
                "normal": exe_path_stylesheet('exe_data/font_stuff/home.svg'),
                "hover": exe_path_stylesheet("exe_data/font_stuff/home_hover.svg"),
                "action": self.go_to_initial_menu,
            },
            {
                "normal": exe_path_stylesheet("exe_data/font_stuff/save.svg"),
                "hover": exe_path_stylesheet("exe_data/font_stuff/save_hover.svg"),
                "action": lambda: self.save_current(True),
            },
            {
                "normal": exe_path_stylesheet("exe_data/font_stuff/rand.svg"),
                "hover": exe_path_stylesheet("exe_data/font_stuff/rand_hover.svg"),
                "action": self.randomize_saved_stamps,
            },
            {
                "normal": exe_path_stylesheet("exe_data/font_stuff/delete.svg"),
                "hover": exe_path_stylesheet("exe_data/font_stuff/delete_hover.svg"),
                "action": lambda: self.toggle_delete_mode(True),
            },
        ]

        self.buttons = []  # Store button references for toggle_delete_mode
        for button_info in buttons:
            button = QPushButton()
            button.setIcon(QIcon(button_info["normal"]))
            button.setIconSize(QSize(72, 72))  # Increased icon size
            button.setFixedSize(96, 96)  # Increased button size
            button.setFlat(True)

            # Add hover effects
            normal_icon = button_info["normal"]
            hover_icon = button_info["hover"]

            button.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                }
                QPushButton:hover {
                    background-color: transparent;
                }
            """)
            button.enterEvent = lambda event, b=button, h=hover_icon: b.setIcon(QIcon(h))
            button.leaveEvent = lambda event, b=button, n=normal_icon: b.setIcon(QIcon(n))

            button.clicked.connect(button_info["action"])
            button_layout.addWidget(button)

            # Save reference for toggle_delete_mode
            button_info["button"] = button
            self.buttons.append(button_info)

        # Spacer under buttons
        spacer = QSpacerItem(20, 16, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.save_menu_layout.addSpacerItem(spacer)

        # Scrollable Grid Layout
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # Hide vertical scroll bar
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.save_menu_layout.addWidget(self.scroll_area)

        # Create a container widget for the grid
        grid_container = QWidget()
        self.grid_layout = QGridLayout()
        grid_container.setLayout(self.grid_layout)
        grid_container.setStyleSheet("background: transparent;")
        self.scroll_area.setWidget(grid_container)

        # Save references
        self.grid_container = grid_container

        self.populate_grid(self.grid_layout)
        self.stacked_widget.addWidget(self.save_menu_widget)

    def populate_grid(self, grid_layout):
        """
        Populates the grid with placeholders and aligns them top-left with 1-pixel borders.
        """
        self.thumbnails = []
        self.loaded_thumbnails = 0
        self.total_thumbnails = len(self.thumbnail_data)

        # Set spacing and margins
        grid_layout.setSpacing(8)
        grid_layout.setContentsMargins(6, 0, 0, 0)  # Margins around the grid

        # Clear any layout alignment constraints to ensure top-left alignment
        grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        for i, thumbnail_data in enumerate(self.thumbnail_data):
            thumbnail_widget = QWidget()
            thumbnail_widget.setFixedSize(128, 128)  # Icon dimensions
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            thumbnail_widget.setLayout(layout)

            # Use the processed WebP preview instead of referencing original files
            preview_path = Path(thumbnail_data["path"])
            if thumbnail_data["is_gif"]:
                # Load GIF as an animated WebP
                gif_label = QLabel()
                gif_movie = QMovie(str(preview_path))  # Use processed WebP
                gif_label.setMovie(gif_movie)
                gif_movie.start()
                layout.addWidget(gif_label)
            else:
                # Load static WebP image
                pixmap = QPixmap(str(preview_path))
                if not pixmap.isNull():
                    image_label = QLabel()
                    image_label.setPixmap(pixmap)  # Set QPixmap object
                    layout.addWidget(image_label)

            thumbnail_widget.setProperty("hash", thumbnail_data["key"])
            thumbnail_widget.mousePressEvent = lambda event, key=thumbnail_data["key"]: self.handle_thumbnail_click(event, key)

            # Ensure widgets align top-left by positioning them explicitly
            row, col = divmod(i, 5)  # 5 columns per row
            grid_layout.addWidget(thumbnail_widget, row, col, alignment=Qt.AlignTop | Qt.AlignLeft)
            self.thumbnails.append(thumbnail_widget)

        print("Grid populated with 1-pixel spacing.")


    def load_visible_thumbnails(self):
        """
        Loads visible thumbnails as the user scrolls or when triggered programmatically.
        """
        print("load_visible_thumbnails triggered")  # Debug statement

        if not hasattr(self, 'thumbnails') or not hasattr(self, 'thumbnail_data'):
            print("Thumbnails or thumbnail data not initialized.")
            return

        scroll_area = self.scroll_area
        visible_area = scroll_area.viewport().rect()
        viewport_top = scroll_area.verticalScrollBar().value()
        viewport_bottom = viewport_top + visible_area.height()

        for i, thumbnail_widget in enumerate(self.thumbnails):
            widget_top = thumbnail_widget.y()
            widget_bottom = widget_top + thumbnail_widget.height()

            # Check if the thumbnail is in the visible range
            if widget_bottom >= viewport_top and widget_top <= viewport_bottom:
                if not thumbnail_widget.property("loaded"):
                    thumbnail_data = self.thumbnail_data[i]
                    layout = thumbnail_widget.layout()

                    # Clear placeholder
                    for j in reversed(range(layout.count())):
                        layout.itemAt(j).widget().deleteLater()

                    if thumbnail_data["is_gif"]:
                        # Create a temporary copy of preview.webp
                        original_path = Path(thumbnail_data["path"])
                        temp_path = original_path.parent / f"temp_{original_path.name}"
                        shutil.copy(str(original_path), str(temp_path))

                        gif_label = QLabel()
                        gif_movie = QMovie(str(temp_path))
                        gif_label.setMovie(gif_movie)
                        gif_movie.start()
                        layout.addWidget(gif_label)

                        # Store reference to QMovie and temp file for later cleanup
                        thumbnail_widget.gif_movie = gif_movie
                        thumbnail_widget.temp_path = temp_path
                    else:
                        # Load static WebP image
                        pixmap = QPixmap(str(thumbnail_data["path"]))
                        if not pixmap.isNull():
                            image_label = QLabel()
                            image_label.setPixmap(pixmap)
                            layout.addWidget(image_label)

                    thumbnail_widget.setProperty("loaded", True)  # Mark as loaded

        print("Lazy loading completed.")


    def lazy_load_thumbnails(self):
        """
        Sets up lazy loading of thumbnails based on scroll position.
        """
        if not self.scroll_area.verticalScrollBar():
            print("Scroll area does not have a vertical scrollbar!")
            return

        self.scroll_area.verticalScrollBar().valueChanged.connect(self.load_visible_thumbnails)
        print("Lazy loading connected to scroll.")

    def load_thumbnail_data(self):
        """
        Load thumbnail data from saved_stamps.json and cache as QPixmap objects.
        """
        # Use the new AppData directory
        appdata_dir = get_appdata_dir()
        saved_stamps_json = appdata_dir / "saved_stamps.json"
        saved_stamps_dir = appdata_dir / "saved_stamps/"

        self.thumbnail_data = []
        self.thumbnail_cache = {}  # Cache thumbnails to avoid direct file references

        if not saved_stamps_json.exists():
            print("No saved_stamps.json file found.")
            return

        try:
            with open(saved_stamps_json, 'r') as f:
                saved_stamps = json.load(f)
        except Exception as e:
            print(f"Error reading saved_stamps.json: {e}")
            return

        for key, value in saved_stamps.items():
            folder_path = saved_stamps_dir / key
            preview_path = folder_path / "preview.webp"

            print(f"Checking path: {preview_path}")
            if preview_path.exists():
                try:
                    pixmap = QPixmap(str(preview_path))
                    if not pixmap.isNull():
                        self.thumbnail_cache[key] = pixmap
                        self.thumbnail_data.append({
                            "path": str(preview_path),  # Keeping for debug or additional logic
                            "is_gif": value.get("is_gif", False),
                            "key": key
                        })
                    else:
                        print(f"Failed to load pixmap for {preview_path}")
                except Exception as e:
                    print(f"Error loading pixmap for {preview_path}: {e}")
            else:
                print(f"Missing preview.webp for key: {key}")

        print(f"Loaded {len(self.thumbnail_data)} thumbnails.")




    def handle_thumbnail_click(self, event, thumbnail_hash):
        """
        Handles a click on a thumbnail, performing an action based on delete mode.
        """
        if getattr(self, 'delete_mode', False):
            # Call the delete function if delete mode is enabled
            self.delete_thumbnail(thumbnail_hash)
        else:
            # Call the load function if delete mode is disabled
            self.load_thumbnail(thumbnail_hash)


    def delete_thumbnail(self, thumbnail_hash):
        """
        Removes the entry for the specified hash from saved_stamps.json.
        """
        # Use the new AppData directory
        appdata_dir = get_appdata_dir()
        saved_stamps_json = appdata_dir / "saved_stamps.json"

        try:
            # Load and update saved_stamps.json
            if saved_stamps_json.exists():
                with open(saved_stamps_json, "r") as json_file:
                    saved_stamps = json.load(json_file)

                if thumbnail_hash in saved_stamps:
                    print(f"Removing entry for hash {thumbnail_hash} from JSON.")
                    del saved_stamps[thumbnail_hash]

                    # Write updated JSON back to file
                    with open(saved_stamps_json, "w") as json_file:
                        json.dump(saved_stamps, json_file, indent=4)
                else:
                    print(f"Hash {thumbnail_hash} not found in JSON.")

                self.show_floating_message("Entry Deleted")
            else:
                print("No saved_stamps.json file found.")
                self.show_floating_message("Error", True)

        except Exception as e:
            print(f"Error while deleting JSON entry: {e}")
            self.show_floating_message("Error", True)

        self.repopulate_grid()

    def load_thumbnail(self, thumbnail_hash):
        """
        Loads a thumbnail by replacing files in the current_stamp_data directory
        with files from the corresponding hash directory.
        """
        # Use the new AppData directory for saved stamps
        appdata_dir = get_appdata_dir()
        saved_stamp_dir = appdata_dir / "saved_stamps" / thumbnail_hash
        current_stamp_dir = exe_path_fs("game_data/current_stamp_data/")

        if not saved_stamp_dir.exists():
            self.show_floating_message("Directory Not Found", True)
            return

        try:
            # Ensure the current_stamp_data directory exists
            current_stamp_dir.mkdir(parents=True, exist_ok=True)

            # Replace stamps.txt if it exists in the hash directory
            stamps_file = saved_stamp_dir / "stamp.txt"
            if stamps_file.exists():
                target_stamps_file = current_stamp_dir / "stamp.txt"
                target_stamps_file.write_text(stamps_file.read_text())

            # Replace frames.txt if it exists in the hash directory
            frames_file = saved_stamp_dir / "frames.txt"
            if frames_file.exists():
                target_frames_file = current_stamp_dir / "frames.txt"
                target_frames_file.write_text(frames_file.read_text())

            self.show_floating_message("Loaded!")

        except Exception as e:
            print(f"Error while loading thumbnail: {e}")
            self.show_floating_message("Error", True)


    def repopulate_grid(self):
        """
        Clears and repopulates the grid layout with updated thumbnail data in order.
        """
        if not hasattr(self, 'grid_layout') or not hasattr(self, 'thumbnail_data'):
            print("Grid layout or thumbnail data not initialized.")
            return

        # Clear existing widgets in the grid
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
                
        for thumbnail_widget in self.thumbnails:
            if hasattr(thumbnail_widget, 'gif_movie'):
                thumbnail_widget.gif_movie.stop()
                thumbnail_widget.gif_movie.deleteLater()
        # Reset thumbnails list and reload data
        self.thumbnails = []
        self.loaded_thumbnails = 0

        self.load_thumbnail_data()  # Reload data from JSON
        self.populate_grid(self.grid_layout)  # Repopulate the grid

        # Trigger immediate visible thumbnail loading
        self.load_visible_thumbnails()
        print("Grid repopulated.")

    def toggle_delete_mode(self, callback = True):
        """
        Toggles delete mode and makes the 'delete' button in the top menu shake
        when delete mode is enabled.
        """
        # Toggle delete mode
        self.delete_mode = not self.delete_mode

        # Find the delete button in the menu
        delete_button = None
        for button_info in self.buttons:
            if button_info["normal"].endswith("delete.svg"):
                delete_button = button_info["button"]
                break

        if not delete_button:
            print("Delete button not found!")
            return

        if self.delete_mode:
            # Create animation for shaking effect
            animation = QPropertyAnimation(delete_button, b"pos")
            animation.setDuration(100)
            animation.setLoopCount(-1)  # Loop indefinitely
            current_pos = delete_button.pos()

            # Define random shaking movement
            animation.setKeyValueAt(0, current_pos)
            animation.setKeyValueAt(0.25, current_pos + QPoint(random.randint(-8, 8), random.randint(-8, 8)))
            animation.setKeyValueAt(0.5, current_pos + QPoint(random.randint(-8, 8), random.randint(-8, 8)))
            animation.setKeyValueAt(0.75, current_pos + QPoint(random.randint(-8, 8), random.randint(-8, 8)))
            animation.setKeyValueAt(1, current_pos)  # Back to center

            animation.start()
            delete_button.animation = animation  # Store reference to prevent garbage collection
        else:
            # Stop shaking
            if hasattr(delete_button, "animation"):
                delete_button.animation.stop()
                del delete_button.animation
                
        if callback:
            if self.delete_mode:
                self.show_floating_message("Click to DELETE", True)
            else:
                self.show_floating_message("Delete Off", True)
            

    def show_save_menu(self):
        """
        Switch to the save menu screen.
        """
        global first 
        if not first:
            cleanup_saved_stamps()
            first = True

        self.repopulate_grid()
        if not hasattr(self, 'thumbnail_data'):
            self.load_thumbnail_data()

        if not hasattr(self, 'save_menu_widget'):
            self.setup_save_menu()
            
        self.stacked_widget.setCurrentWidget(self.save_menu_widget)

        self.delete_mode = True
        self.toggle_delete_mode(False)

    def close_application(self):
        for timer in self.color_timers.values():
            timer.stop()
        self.close()
        

    def update_cluster_label(self):
        """
        Updates the cluster count label dynamically as the slider changes.
        """
        self.cluster_label_value.setText(str(self.cluster_slider.value()))


    def retry_processing(self):
        
        if not hasattr(self, 'secondary_widget'):
            self.setup_secondary_menu()
            
        self.stacked_widget.setCurrentWidget(self.secondary_widget)
        # Ensure the back button is visible and brought to the front
        if self.back_button:
            self.back_button.show()
            self.back_button.raise_()
        if self.refresh_button:
            self.refresh_button.show()
            self.refresh_button.raise_()

    def resize_slider_changed(self, value):
        self.resize_value_label.setText(str(value))

    def setup_color_options_ui(self, layout):
        """
        Creates a 1x6 grid layout of color options with:
        - Color squares (100x100) in a 1x6 grid.
        - Options (Enable, RGB, Blank checkboxes) inside each color square.
        - Boost and Threshold labels and sliders horizontally aligned beneath each color square.
        - Boost text and slider appear only when Preprocess Image is checked.
        """
        
        # Helper function to determine text color based on background color
        def get_contrast_color(hex_color):
            """
            Returns 'white' or 'black' based on the luminance of the provided hex color.
            """
            # Convert hex to RGB
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 6:
                r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            elif len(hex_color) == 3:
                r, g, b = tuple(int(hex_color[i]*2, 16) for i in range(3))
            else:
                # Default to white if format is unexpected
                return 'white'
            
            # Calculate luminance using the formula
            luminance = (0.299 * r + 0.587 * g + 0.114 * b)
            return 'white' if luminance < 100 else 'black'

        # Decorative ring-style border for the entire section
        ring_frame = QFrame()
        ring_frame.setStyleSheet("""
            QFrame {
                border: 4px solid #7b1fa2; /* Increased Purple border */
                border-radius: 15px;
                padding: 4px; /* Inner padding */
                margin: 0px;  /* Outer margin */
            }
        """)
        ring_layout = QVBoxLayout()
        ring_layout.setSpacing(1)  # Increased spacing for better layout
        ring_layout.setContentsMargins(5, 5, 5, 5)  # Increased inner padding
        ring_frame.setLayout(ring_layout)

        # Grid layout for color squares
        color_layout = QGridLayout()
        color_layout.setHorizontalSpacing(1)  # Adjusted horizontal spacing
        color_layout.setVerticalSpacing(25)    # Adjusted vertical spacing for better alignment
        ring_layout.addLayout(color_layout)

        # Initialize dictionaries to track widgets and border colors
        self.color_checkboxes = {}
        self.rgb_checkboxes = {}
        self.blank_checkboxes = {}
        self.boost_sliders = {}
        self.boost_labels = {}
        self.threshold_sliders = {}
        self.threshold_labels = {}
        self.color_labels = {}
        self.color_timers = {}  # Initialize timers for RGB animation
        self.border_colors = {}  # Store original border colors

        # Track the currently selected Boost and Blank
        self.current_boost_color = None
        self.current_blank_color = None

        # Create the 1x6 grid of color options
        for i, color in enumerate(self.default_color_key_array):
            color_number = color['number']  # Use the actual color number from the array
            color_hex = color['hex']

            text_color = get_contrast_color(color_hex)

            # Dynamically set icons based on text color
            if text_color == "white":
                unchecked_icon = exe_path_stylesheet('exe_data/font_stuff/uncheck_white.svg')
                checked_icon = exe_path_stylesheet('exe_data/font_stuff/check_white.svg')
                border_color = "#ffffff"  # White border for light text
            else:
                unchecked_icon = exe_path_stylesheet('exe_data/font_stuff/uncheck.svg')
                checked_icon = exe_path_stylesheet('exe_data/font_stuff/check.svg')
                border_color = "#e3a8e6"

            # Store the border color
            self.border_colors[color_number] = border_color

            # Create a container widget for the color box and its options
            color_container = QWidget()
            color_container_layout = QVBoxLayout()
            color_container_layout.setAlignment(Qt.AlignTop)
            color_container_layout.setSpacing(10)
            color_container_layout.setContentsMargins(0, 0, 0, 0)
            color_container.setLayout(color_container_layout)

            # Color box (replacing QLabel with QWidget)
            color_box = QWidget()
            color_box.setFixedSize(100, 100)
            color_box.setStyleSheet(f"""
                QWidget {{
                    background-color: #{color_hex};
                    border: 4px solid {border_color}; /* Dynamic border color */
                    border-radius: 10px;
                }}
            """)
            color_container_layout.addWidget(color_box, alignment=Qt.AlignCenter)

            # Layout for checkboxes inside the color box
            checkbox_layout = QVBoxLayout()
            checkbox_layout.setSpacing(8)
            checkbox_layout.setContentsMargins(8, 10, 5, 5)  # Offset checkboxes by 5 pixels right and down
            color_box.setLayout(checkbox_layout)

            # Define a common stylesheet template for the checkboxes
            checkbox_stylesheet = f"""
                QCheckBox {{
                    color: {text_color}; /* Dynamic text color based on background */
                    font-size: 15px; /* Adjusted font size */
                    font-weight: bold;
                    background: transparent; /* Ensure no background */
                    border: none; /* Remove any border/frame */
                }}
                QCheckBox::indicator {{
                    width: 20px;
                    height: 20px;
                }}
                QCheckBox::indicator:unchecked {{
                    image: url({unchecked_icon});
                }}
                QCheckBox::indicator:checked {{
                    image: url({checked_icon});
                }}
            """

            # Enable checkbox
            enable_checkbox = QCheckBox("Enable")
            enable_checkbox.setChecked(True)
            enable_checkbox.setStyleSheet(checkbox_stylesheet)
            enable_checkbox.toggled.connect(
                lambda checked, num=color_number: self.toggle_enable_options(num, checked)
            )
            self.color_checkboxes[color_number] = enable_checkbox
            checkbox_layout.addWidget(enable_checkbox)

            # RGB checkbox
            rgb_checkbox = QCheckBox("RGB")
            rgb_checkbox.setStyleSheet(checkbox_stylesheet)
            rgb_checkbox.toggled.connect(
                lambda checked, num=color_number: self.toggle_rgb(num, checked)
            )
            self.rgb_checkboxes[color_number] = rgb_checkbox
            checkbox_layout.addWidget(rgb_checkbox)

            # Blank checkbox
            blank_checkbox = QCheckBox("Blank")
            blank_checkbox.setStyleSheet(checkbox_stylesheet)
            blank_checkbox.toggled.connect(
                lambda checked, num=color_number: self.toggle_blank(num, checked)
            )
            self.blank_checkboxes[color_number] = blank_checkbox
            checkbox_layout.addWidget(blank_checkbox)


            # Spacer to push checkboxes to the top
            checkbox_layout.addStretch()

            # Boost label
            boost_label = QLabel("Boost")
            boost_label.setAlignment(Qt.AlignCenter)
            boost_label.setStyleSheet("""
                QLabel {
                    color: white; /* Always white */
                    font-size: 17px; /* Adjusted font size */
                    font-weight: bold;
                    border: none; /* No ring */
                    margin-bottom: 0px; /* Reduce bottom margin */
                    padding-bottom: 0px; /* Reduce bottom padding */
                    background: transparent; /* Ensure no background */
                }
            """)
            boost_label.setVisible(False)
            self.boost_labels[color_number] = boost_label
            color_container_layout.addWidget(boost_label)

            # Boost slider
            boost_slider = QSlider(Qt.Horizontal)
            boost_slider.setRange(-1, 27)
            boost_slider.setValue(14)
            boost_slider.setTickInterval(1)
            boost_slider.setTickPosition(QSlider.TicksBelow)
            boost_slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 6px;
                    background: #7b1fa2;
                    border-radius: 3px;
                }
                QSlider::handle:horizontal {
                    background: #ffffff;
                    border: 1px solid #7b1fa2;
                    width: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }
            """)
            boost_slider.setVisible(False)
            self.boost_sliders[color_number] = boost_slider
            boost_slider.setFixedWidth(100)  # Set the width to match the color box
            color_container_layout.addWidget(boost_slider, alignment=Qt.AlignCenter)

            # Threshold label
            threshold_label = QLabel("Threshold")
            threshold_label.setAlignment(Qt.AlignCenter)
            threshold_label.setStyleSheet("""
                QLabel {
                    color: white; /* Always white */
                    font-size: 14px; /* Adjusted font size */
                    font-weight: bold;
                    border: none; /* No ring */
                    margin-bottom: 0px; /* Reduce bottom margin */
                    padding-bottom: 0px; /* Reduce bottom padding */
                    background: transparent; /* Ensure no background */
                }
            """)
            threshold_label.setVisible(False)
            self.threshold_labels[color_number] = threshold_label
            color_container_layout.addWidget(threshold_label)

            # Threshold slider
            threshold_slider = QSlider(Qt.Horizontal)
            threshold_slider.setRange(0, 100)
            threshold_slider.setValue(20)
            threshold_slider.setTickInterval(1)
            threshold_slider.setTickPosition(QSlider.TicksBelow)
            threshold_slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 6px;
                    background: #7b1fa2;
                    border-radius: 3px;
                }
                QSlider::handle:horizontal {
                    background: #ffffff;
                    border: 1px solid #7b1fa2;
                    width: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }
            """)
            threshold_slider.setVisible(False)
            self.threshold_sliders[color_number] = threshold_slider
            threshold_slider.setFixedWidth(100)  # Set the width to match the color box
            color_container_layout.addWidget(threshold_slider, alignment=Qt.AlignCenter)

            # Add a spacer to ensure Boost and Threshold space remains consistent
            color_container_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

            # Assign the color_box to self.color_labels for reference
            self.color_labels[color_number] = color_box

            # Add the color container to the grid layout
            row = 0
            col = i
            color_layout.addWidget(color_container, row, col)

        # Add the ring frame to the parent layout
        layout.addWidget(ring_frame, alignment=Qt.AlignBottom)

        # Connect toggle functions
        self.preprocess_checkbox.toggled.connect(self.toggle_boost_elements)
        self.lab_color_checkbox.toggled.connect(self.lab_value_toggle)


    def toggle_enable_options(self, color_number, enabled):
        """
        Enables/disables RGB and Blank checkboxes based on the state of the Enable checkbox.
        Prevents disabling the last enabled color.
        """
        # Ensure at least one color remains enabled
        if not enabled and all(not cb.isChecked() for cb in self.color_checkboxes.values()):
            self.color_checkboxes[color_number].setChecked(True)
            QMessageBox.warning(self, "Warning", "At least one color must remain enabled.")
            return

        # Show or hide the RGB and Blank checkboxes based on Enable state
        self.rgb_checkboxes[color_number].setVisible(enabled)
        self.blank_checkboxes[color_number].setVisible(enabled)

        if enabled and self.preprocess_checkbox.isChecked():
            self.boost_labels[color_number].setVisible(True)
            self.boost_sliders[color_number].setVisible(True)
            self.threshold_labels[color_number].setVisible(True)
            self.threshold_sliders[color_number].setVisible(True)
        else:
            self.boost_labels[color_number].setVisible(False)
            self.boost_sliders[color_number].setVisible(False)
            self.threshold_labels[color_number].setVisible(False)
            self.threshold_sliders[color_number].setVisible(False)


    def toggle_boost_elements(self, checked):
        """
        Toggles the visibility of Boost labels and sliders based on the state of the Preprocess Image checkbox.
        They only reappear if their corresponding color box is enabled.
        """
        if checked:
            self.brightness_label.setVisible(True)
            self.brightness_slider.setVisible(True)
        else:
            self.brightness_label.setVisible(False)
            self.brightness_slider.setVisible(False)

        for color_number in self.boost_labels:
            if checked and self.color_checkboxes[color_number].isChecked():  
                self.boost_labels[color_number].setVisible(True)
                self.boost_sliders[color_number].setVisible(True)
                self.threshold_labels[color_number].setVisible(True)
                self.threshold_sliders[color_number].setVisible(True)
            else:  # Hide if preprocessing is disabled or the color box is not enabled
                self.boost_labels[color_number].setVisible(False)
                self.boost_sliders[color_number].setVisible(False)
                self.threshold_labels[color_number].setVisible(False)
                self.threshold_sliders[color_number].setVisible(False)


    def toggle_blank(self, color_number, checked):
        """
        Toggles the border visibility of the color square based on the Blank checkbox state.
        Ensures mutual exclusivity with the RGB checkbox.
        """
        # Find the corresponding color data
        color_data = next((color for color in self.default_color_key_array if color['number'] == color_number), None)
        if not color_data:
            print(f"Error: No color data found for color_number {color_number}")
            return

        if checked:
            # Uncheck the currently active blank if there is one
            if self.current_blank_color is not None and self.current_blank_color != color_number:
                self.blank_checkboxes[self.current_blank_color].setChecked(False)
            self.current_blank_color = color_number
            self.rgb_checkboxes[color_number].setChecked(False)
            self.color_labels[color_number].setStyleSheet(f"""
                QWidget {{
                    background-color: #{color_data['hex']};
                    border: none;
                    border-radius: 10px;
                }}
            """)
        else:
            self.current_blank_color = None
            # Use the stored border color instead of hardcoding
            border_color = self.border_colors.get(color_number, "#ffffff")
            self.color_labels[color_number].setStyleSheet(f"""
                QWidget {{
                    background-color: #{color_data['hex']};
                    border: 4px solid {border_color};
                    border-radius: 10px;
                }}
            """)


    def toggle_rgb(self, color_number, checked):
        """
        Toggles RGB animation for the color square and ensures mutual exclusivity with the Blank checkbox.
        """
        # Find the corresponding color data
        color_data = next((color for color in self.default_color_key_array if color['number'] == color_number), None)
        if not color_data:
            print(f"Error: No color data found for color_number {color_number}")
            return

        if checked:
            # Uncheck the currently active RGB if there is one
            if self.current_boost_color is not None and self.current_boost_color != color_number:
                self.rgb_checkboxes[self.current_boost_color].setChecked(False)
            self.current_boost_color = color_number
            self.blank_checkboxes[color_number].setChecked(False)

            # Stop existing timers for the color to avoid duplicates
            if color_number in self.color_timers:
                self.color_timers[color_number].stop()
                del self.color_timers[color_number]

            # Start the RGB animation
            timer = QTimer(self)
            timer.setInterval(100)  
            timer.timeout.connect(lambda: self.update_rgb_border(color_number))
            self.color_timers[color_number] = timer
            timer.start()
        else:
            # Stop the RGB animation and reset the border
            if color_number in self.color_timers:
                self.color_timers[color_number].stop()
                del self.color_timers[color_number]

            self.current_boost_color = None
            # Use the stored border color instead of hardcoding
            border_color = self.border_colors.get(color_number, "#ffffff")
            self.color_labels[color_number].setStyleSheet(f"""
                QWidget {{
                    background-color: #{color_data['hex']};
                    border: 4px solid {border_color};
                    border-radius: 10px;
                }}
            """)


    def update_rgb_border(self, color_number):
        """
        Updates the border color of the specified color square to cycle through RGB.
        """
        color_label = self.color_labels[color_number]
        rgb_cycle = ["red", "green", "blue"]

        # Extract current border color from the stylesheet
        current_style = color_label.styleSheet()
        current_color = next((color for color in rgb_cycle if f"border: 4px solid {color}" in current_style), None)
        if current_color is None:
            next_color = "red"
        else:
            next_color = rgb_cycle[(rgb_cycle.index(current_color) + 1) % len(rgb_cycle)]

        # Get the background color
        color_data = next(
            (color for color in self.default_color_key_array if color['number'] == color_number),
            None
        )
        if not color_data:
            hex_color = "ffffff"
        else:
            hex_color = color_data['hex']

        # Update the widget's stylesheet with the next RGB color
        color_label.setStyleSheet(
            f"background-color: #{hex_color}; border: 4px solid {next_color}; border-radius: 10px;"
        )


    def open_image_from_files(self):# Clear any previous states
        # Open file dialog
        if not hasattr(self, 'secondary_widget'):
            self.setup_secondary_menu()
        self.reset_to_initial_state()     
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilters(["Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"])
        if file_dialog.exec():
            file_path = file_dialog.selectedFiles()[0]
            self.image_path = file_path
            self.load_image(file_path)

            if not hasattr(self, 'secondary_widget'):
                self.setup_secondary_menu()
                
            self.stacked_widget.setCurrentWidget(self.secondary_widget)

    def open_image_from_menu(self, path):
        if not hasattr(self, 'secondary_widget'):
            self.setup_secondary_menu()
        self.reset_to_initial_state()   
        self.image_path = path
        self.load_image(path)
        self.stacked_widget.setCurrentWidget(self.secondary_widget)

    def open_image_from_clipboard(self, center = False):
        """
        Handles retrieving image content from the clipboard, ensuring proper handling of
        both static and animated images. Saves the clipboard image as a WebP file for further processing.
        """
        if not hasattr(self, 'secondary_widget'):
            self.setup_secondary_menu()

        self.canpaste = False
        try:
            # Step 1: Retrieve clipboard content
            clipboard_content = ImageGrab.grabclipboard()
            if clipboard_content is None:
                self.show_floating_message("No image found", center)  # Floating message instead of error popup
                self.canpaste = True
                return

            # Step 2: Handle clipboard containing file paths
            if isinstance(clipboard_content, list):
                # Filter for valid image files
                image_files = [f for f in clipboard_content if os.path.isfile(f)]
                if not image_files:
                    self.show_floating_message("No image found", center)  # Floating message
                    self.canpaste = True
                    return

                # Open the first image file in the list
                img = Image.open(image_files[0])

            # Step 3: Handle clipboard containing a direct image
            elif isinstance(clipboard_content, Image.Image):
                img = clipboard_content
            else:
                self.show_floating_message("Clipboard does not contain an image or image file") 
                self.canpaste = True
                return
            
            self.reset_to_initial_state()   
            self.canpaste = False
            # Step 4: Detect if the image is animated
            is_multiframe = getattr(img, "is_animated", False)
            # Step 5: Save the image to a temporary WebP file in directory
            temp_image_path = exe_directory / 'temp' / 'clipboard_image.webp'
            if is_multiframe:
                # Save as an animated WebP
                img.save(temp_image_path, format="WEBP", save_all=True, duration=img.info.get("duration", 100), loop=img.info.get("loop", 0))
            else:
                # Save as a static WebP
                img.save(temp_image_path, format="WEBP")
            print(temp_image_path)
            # Step 6: Pass the temporary file path to load_image
            self.image_path = temp_image_path  # Indicate clipboard source
            self.load_image(temp_image_path)  # Treat like a regular image or GIF
            self.stacked_widget.setCurrentWidget(self.secondary_widget)
            self.canpaste = True

        except Exception as e:
            self.show_floating_message(f"Failed to process clipboard: {str(e)}") 
            self.canpaste = True

    def reset_color_options(self):

        for color_number in self.color_checkboxes.keys():
            # Enable all colors
            self.color_checkboxes[color_number].setChecked(True)
            # Disable RGB and Blank checkboxes
            self.rgb_checkboxes[color_number].setChecked(False)
            self.rgb_checkboxes[color_number].setVisible(True)  # Ensure visibility
            self.blank_checkboxes[color_number].setChecked(False)
            self.blank_checkboxes[color_number].setVisible(True)  # Ensure visibility

            # Reset Boost sliders to 1.2 (value 12)
            if color_number in self.boost_sliders:
                self.boost_sliders[color_number].setValue(14)
                self.boost_sliders[color_number].setVisible(True)  # Reset visibility

            # Reset Threshold sliders to 20
            if color_number in self.threshold_sliders:
                self.threshold_sliders[color_number].setValue(20)
                self.threshold_sliders[color_number].setVisible(True)  # Reset visibility

            # Hide Boost and Threshold labels
            if color_number in self.boost_labels:
                self.boost_labels[color_number].setVisible(True)
            if color_number in self.threshold_labels:
                self.threshold_labels[color_number].setVisible(True)

        if self.is_gif:
            if "Color Match" in [method["name"] for method in self.processing_methods]:
                self.processing_combobox.setCurrentText("Color Match")
                self.processing_method_changed("Color Match")

        else:
            if "Pattern Dither" in [method["name"] for method in self.processing_methods]:
                self.processing_combobox.setCurrentText("Pattern Dither")
                self.processing_method_changed("Pattern Dither", True)

        self.preprocess_checkbox.setChecked(True)
        self.lab_color_checkbox.setChecked(False)
        self.bg_removal_checkbox.setChecked(False)
        self.brightness_label.setVisible(True)
        self.brightness_slider.setVisible(True)

        for color_number in self.boost_labels:
                self.boost_labels[color_number].setVisible(True)
                self.boost_sliders[color_number].setVisible(True)
                self.threshold_labels[color_number].setVisible(True)
                self.threshold_sliders[color_number].setVisible(True)



    def lab_value_toggle(self, checked):
        for color_number in self.boost_labels:
            if checked:
                if color_number in self.boost_sliders:
                    self.boost_sliders[color_number].setValue(14)

                # Reset Threshold sliders to 20
                if color_number in self.threshold_sliders:
                    self.threshold_sliders[color_number].setValue(20)


            else:

                if color_number in self.boost_sliders:
                    self.boost_sliders[color_number].setValue(12)


                # Reset Threshold sliders to 20
                if color_number in self.threshold_sliders:
                    self.threshold_sliders[color_number].setValue(28)



    @Slot(str, bool)
    def show_floating_message(self, message, centered=False):
        """
        Creates a floating particle effect for the given message.
        The message drifts upward with exaggerated Y-axis movement, random wandering on the X-axis,
        sporadic chaotic movement, and an additional chance for extreme rapid shaking.

        Parameters:
            message (str): The text to display.
            centered (bool): If True, the message originates from the center of the GUI near the bottom.
        """
        # Doge meme-inspired colors
        doge_colors = ['#FFDD00', '#FF4500', '#1E90FF', '#32CD32', '#FF69B4', '#9400D3']

        # Create the floating label
        label = QLabel(message, self)
        random_color = random.choice(doge_colors)
        label.setStyleSheet(f"""
            QLabel {{
                color: {random_color};  /* Doge color */
                font-size: 36px;  /* Larger text */
                font-weight: 900; /* Extra bold */
                background-color: transparent;
            }}
        """)
        label.setAttribute(Qt.WA_TransparentForMouseEvents)  # Ignore mouse events
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)  # Enable text wrapping

        # Adjust size to fit the GUI width
        max_width = self.width() - 40  # Allow padding from the edges
        label.setFixedWidth(max_width)
        label.adjustSize()

        # Calculate start position
        if centered:
            label_width = label.width()
            label_height = label.height()
            start_x = (self.width() - label_width) // 2
            start_y = self.height() - 50 - label_height
            start_pos = QPoint(start_x, start_y)
        else:
            # Start position: center the label on the cursor
            cursor_pos = self.mapFromGlobal(QCursor.pos())
            label_width = label.width()
            label_height = label.height()
            start_pos = QPoint(cursor_pos.x() - label_width // 2, cursor_pos.y() - label_height // 2)

        label.move(start_pos)
        label.show()

        # Random tilt (±5 degrees)
        rotation_angle = random.uniform(-5, 5)
        label.setStyleSheet(label.styleSheet() + f"""
            transform: rotate({rotation_angle}deg);
        """)

        # Animation: Exaggerated upward movement with sporadic chaos
        move_animation = QPropertyAnimation(label, b"pos", self)
        move_animation.setDuration(5000)  # 5 seconds
        move_animation.setStartValue(start_pos)

        # Randomized end position with large vertical drift and sporadic horizontal wandering
        end_x = start_pos.x() + random.randint(-100, 100)  # Wider horizontal range
        end_y = start_pos.y() - random.randint(600, 1000)  # Extreme upward drift

        # Add a chance for chaotic movement
        if random.random() < 0.3:  # 30% chance for sporadic movement
            mid_x = start_pos.x() + random.randint(-200, 200)
            mid_y = start_pos.y() - random.randint(200, 400)
            move_animation.setKeyValueAt(0.5, QPoint(mid_x, mid_y))  # Insert chaos mid-way
            

        # Add a chance for continuous rapid shaking
        if random.random() < 0.05:
            for i in range(70):
                shake_x = start_pos.x() + random.randint(-100, 100)
                shake_y = start_pos.y() - (i * (start_pos.y() - end_y) // 40) + random.randint(-30, 30)
                move_animation.setKeyValueAt(i / 70, QPoint(shake_x, shake_y))

        move_animation.setEndValue(QPoint(end_x, end_y))
        move_animation.setEasingCurve(QEasingCurve.OutQuad)

        # Animation: Fade out the label
        fade_animation = QPropertyAnimation(label, b"windowOpacity", self)
        fade_animation.setDuration(5000)  # 5 seconds
        fade_animation.setStartValue(1)  # Fully opaque
        fade_animation.setEndValue(0)  # Fully transparent

        # Start both animations
        move_animation.start()
        fade_animation.start()

        # Ensure the label is deleted after the animations are done
        fade_animation.finished.connect(label.deleteLater)


    def load_image(self, file_path):
        """
        Loads an image file, properly handling static and animated formats (GIF, WebP).
        """     
        try:
            # Open the image with PIL
            img = Image.open(file_path)
            is_animated = getattr(img, "is_animated", False)

            if is_animated:
                # Handle animated content
                self.is_gif = True
                self.image_path = file_path

                image_width, image_height = img.size

                max_dimension = max(image_width, image_height)  # Use the larger dimension

                if max_dimension > 200:
                    self.resize_slider.setMaximum(200)
                    self.resize_slider.setValue(100)
                else:
                    self.resize_slider.setMaximum(200)
                    self.resize_slider.setValue(max_dimension)

                self.display_gif(file_path)

            else:
                # Handle static content
                self.is_gif = False
                img = img.convert("RGBA")  # Ensure consistent format

                # Get the width and height of the image using PIL
                image_width, image_height = img.size
                max_dimension = max(image_width, image_height)  # Use the larger dimension

                # Adjust the resize slider based on the maximum dimension
                if max_dimension > 400:
                    self.resize_slider.setMaximum(400)
                    self.resize_slider.setValue(128)
                else:
                    self.resize_slider.setMaximum(400)
                    self.resize_slider.setValue(max_dimension)

                self.image = ImageQt.ImageQt(img)  # Convert PIL image to QImage
                self.display_image()
            

            
            for color_number in self.color_checkboxes.keys():
                # Enable all colors
                self.color_checkboxes[color_number].setChecked(True)

                # Disable RGB and Blank checkboxes
                self.rgb_checkboxes[color_number].setChecked(False)
                self.rgb_checkboxes[color_number].setVisible(True)  # Ensure visibility
                self.blank_checkboxes[color_number].setChecked(False)
                self.blank_checkboxes[color_number].setVisible(True)  # Ensure visibility

                # Reset Boost sliders to 1.2 (value 12)
                if color_number in self.boost_sliders:
                    self.boost_sliders[color_number].setValue(12)
                    self.boost_sliders[color_number].setVisible(True)  # Reset visibility

                # Reset Threshold sliders to 20
                if color_number in self.threshold_sliders:
                    self.threshold_sliders[color_number].setValue(28)
                    self.threshold_sliders[color_number].setVisible(True)  # Reset visibility

                # Hide Boost and Threshold labels
                if color_number in self.boost_labels:
                    self.boost_labels[color_number].setVisible(True)
                if color_number in self.threshold_labels:
                    self.threshold_labels[color_number].setVisible(True)

            self.preprocess_checkbox.setChecked(True)
            self.bg_removal_checkbox.setChecked(False)
            self.lab_color_checkbox.setChecked(False)

            if self.back_button:
                self.back_button.show()
                self.back_button.raise_()
            if self.refresh_button:
                self.refresh_button.show()
                self.refresh_button.raise_()
            if self.is_gif:
                if "Color Match" in [method["name"] for method in self.processing_methods]:
                    self.processing_combobox.setCurrentText("Color Match")
                    self.processing_method_changed("Color Match")

            else:
                if "Pattern Dither" in [method["name"] for method in self.processing_methods]:
                    self.processing_combobox.setCurrentText("Pattern Dither")
                    self.processing_method_changed("Pattern Dither", True)

            for color_number in self.boost_labels:
                self.boost_labels[color_number].setVisible(True)
                self.boost_sliders[color_number].setVisible(True)
                self.threshold_labels[color_number].setVisible(True)
                self.threshold_sliders[color_number].setVisible(True)

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load image: {str(e)}")


    def display_gif(self, file_path):
        """
        Processes and displays an animated GIF or WebP with fixed dimensions (416x376).
        The GIF retains its aspect ratio, with one dimension reaching 416 or 376, and is aligned
        bottom-center in the larger frame. Downscaling uses bicubic; upscaling uses nearest neighbor.
        Frame delays are preserved to maintain the original animation speed.
        """
        # Ensure the passed widget is a QLabel
        if not isinstance(self.image_label, QLabel):
            raise ValueError("The 'image_label' argument must be an instance of QLabel.")

        # Define fixed dimensions
        frame_width, frame_height = 416, 256

        # Temporary file for the resized GIF
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webp")
        temp_path = temp_file.name

        with Image.open(file_path) as img:
            # Determine aspect ratio and new dimensions
            img_width, img_height = img.size
            aspect_ratio = img_width / img_height

            if aspect_ratio > 1:  # Wider than tall
                new_width = frame_width
                new_height = int(frame_width / aspect_ratio)
                if new_height > frame_height:
                    new_height = frame_height
                    new_width = int(new_height * aspect_ratio)
            else:  # Taller than wide
                new_height = frame_height
                new_width = int(frame_height * aspect_ratio)
                if new_width > frame_width:
                    new_width = frame_width
                    new_height = int(new_width / aspect_ratio)

            # Calculate offsets for bottom-center alignment
            x_offset = (frame_width - new_width) // 2
            y_offset = frame_height - new_height

            # Create a blank RGBA canvas for the fixed frame dimensions
            blank_frame = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))

            # Process all frames and preserve frame delays
            frames = []
            delays = []
            for frame in ImageSequence.Iterator(img):
                frame = frame.convert("RGBA")  # Ensure consistent format
                resample_method = (
                    Image.Resampling.BICUBIC if img_width > new_width or img_height > new_height else Image.Resampling.NEAREST
                )
                resized_frame = frame.resize((new_width, new_height), resample=resample_method)

                # Paste resized frame onto the blank canvas
                positioned_frame = blank_frame.copy()
                positioned_frame.paste(resized_frame, (x_offset, y_offset), resized_frame)
                frames.append(positioned_frame)

                # Preserve frame delay (default to 100ms if not provided)
                delays.append(frame.info.get("duration", 100))

            # Save the frames as a new WebP animation with preserved delays
            frames[0].save(
                temp_path,
                format="WEBP",
                save_all=True,
                append_images=frames[1:],
                loop=0,
                duration=delays,
                disposal=2  # Clear previous frames
            )

        # Load the resized GIF into QMovie
        movie = QMovie(temp_path)

        # Configure QLabel appearance and alignment
        self.image_label.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)
        self.image_label.setStyleSheet("background-color: transparent; border: none;")

        # Set the QMovie to the QLabel and start the animation
        self.image_label.setMovie(movie)
        movie.start()




    def display_image(self):
        """
        Displays a static image resized to 420x420.
        Nearest-neighbor scaling is applied for upscaling, and bicubic scaling is used for downscaling.
        """
        if not self.image:
            QMessageBox.warning(self, "Error", "No image available to display.")
            return

        # Convert QImage to QPixmap
        pixmap = QPixmap.fromImage(self.image)

        # Determine scaling method
        if pixmap.width() > 416 or pixmap.height() > 256:
            # Downscaling: Use smooth transformation (bicubic)
            transformation_mode = Qt.SmoothTransformation
        else:
            # Upscaling: Use fast transformation (nearest-neighbor)
            transformation_mode = Qt.FastTransformation

        # Resize the image to 420x380
        resized_pixmap = pixmap.scaled(
            416, 256, Qt.KeepAspectRatio, transformation_mode
        )

        # Display the image in the QLabel
        self.image_label.setPixmap(resized_pixmap)
        self.image_label.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)
        self.image_label.setStyleSheet("background-color: transparent; border: none;")  # Ensure no black border

            
    def processing_method_changed(self, method_name, strength = False):
        
        """
        Updates the parameter input UI dynamically when the processing method is changed.
        Handles descriptions and ensures compatibility with the new decorator structure.
        """
        # Clear existing parameter widgets
        # Clear existing parameter widgets completely
        while self.method_options_layout.rowCount() > 0:
            self.method_options_layout.removeRow(0)
        self.parameter_widgets.clear()

        # Retrieve the processing function
        processing_function = processing_method_registry.get(method_name)
        if not processing_function:
            return

        # Retrieve the description and display it if needed
        method_description = getattr(processing_function, "description", "")
        if method_description:
            #self.description_box.setText(method_description)
            # Description Box
            #self.description_box = QLabel()
            #self.description_box.setStyleSheet("""
            #    QLabel {
            #        color: white; /* White text color */
            #        font-size: 13px; /* Normal font size */
            #        background: transparent; /* No background */
            #        border: none; /* No border */
            #        padding: 0px; /* Minimal padding */
            #        margin: 0px; /* Minimal margin */
            #        text-align: left; /* Align text to the left */
            #    }
            #""")
            #self.description_box.setWordWrap(True)  # Enable text wrapping for multiline support
            #self.description_box.setAlignment(Qt.AlignLeft | Qt.AlignTop)  # Align text to the top-left
            #self.description_box.setText("This is your description text. Replace this with the desired content.")
            #ring_layout.addWidget(self.description_box)
            pass
        # Retrieve default parameters
        default_params = getattr(processing_function, "default_params", {})

        # Dynamically create input widgets for parameters
        for param_name, default_value in default_params.items():
            label = QLabel(f"{method_name} {param_name.capitalize()}:")
            
            # Special handling for 'Clusters'
            if param_name == 'Clusters':
                # Create slider
                slider = QSlider(Qt.Horizontal)
                slider.setRange(2, 16)  # Range for clusters
                slider.setValue(12) 
                slider.setTickPosition(QSlider.TicksBelow)
                slider.setTickInterval(1)  # Step size for clusters
                slider.valueChanged.connect(self.parameter_value_changed)

                # Display value dynamically
                value_label = QLabel(str(slider.value()))
                value_label.setAlignment(Qt.AlignCenter)
                value_label.setFixedWidth(60)

                # Combine slider and value label into a horizontal layout
                slider_layout = QHBoxLayout()
                slider_layout.addWidget(slider)
                slider_layout.addWidget(value_label)

                # Update value label when the slider changes
                def update_value_label(value):
                    if value < 16:
                        value_label.setText(str(value))
                    else:
                        value_label.setText("Lots")
                slider.valueChanged.connect(update_value_label)
                update_value_label(12)
                # Add slider layout to the form
                self.method_options_layout.addRow(label, slider_layout)

                # Save the slider to parameter widgets
                self.parameter_widgets[param_name] = slider


            elif isinstance(default_value, (float, int)):
                slider = QSlider(Qt.Horizontal)
                if isinstance(default_value, float):
                    slider.setRange(0, 100)
                    slider.setValue(int(default_value * 100))
                    slider.setTickInterval(10)

                else:
                    slider.setRange(1, 100)
                    slider.setValue(default_value)
                    slider.setTickInterval(10)
                slider.setTickPosition(QSlider.TicksBelow)
                slider.valueChanged.connect(self.parameter_value_changed)
                self.method_options_layout.addRow(label, slider)
                self.parameter_widgets[param_name] = slider
            elif isinstance(default_value, bool):
                # Use a checkbox for boolean values
                checkbox = QCheckBox()
                checkbox.setChecked(default_value)
                self.method_options_layout.addRow(label, checkbox)
                self.parameter_widgets[param_name] = checkbox
            elif isinstance(default_value, str):
                if param_name in ["line_color", "line_style"]:
                    # Use a combo box for predefined options
                    combo_box = QComboBox()
                    if param_name == "line_color":
                        combo_box.addItems(["auto", "black", "white"])
                    elif param_name == "line_style":
                        combo_box.addItems(["black_on_white", "white_on_black"])
                    combo_box.setCurrentText(default_value)
                    self.method_options_layout.addRow(label, combo_box)
                    self.parameter_widgets[param_name] = combo_box
                else:
                    line_edit = QLineEdit(default_value)
                    self.method_options_layout.addRow(label, line_edit)
                    self.parameter_widgets[param_name] = line_edit
            else:
                # Fallback for unsupported types
                line_edit = QLineEdit(str(default_value))
                self.method_options_layout.addRow(label, line_edit)
                self.parameter_widgets[param_name] = line_edit

                

    def parameter_value_changed(self, value):
        # Update any dependent UI elements if necessary
        pass

    def process_image(self):
        if not hasattr(self, 'result_widget'):
            self.setup_result_menu()
            
        if not self.image_path:
            QMessageBox.warning(self, "Error", "No image selected.")
            return

        self.status_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.action_layout.setCurrentIndex(1)  # Show status layout
        self.status_label.setText("Starting processing...")
        # Hide back button
        if self.back_button:
            self.back_button.hide()
        if self.refresh_button:
            self.refresh_button.hide()
        self.canpaste = False
        # Collect parameters

        preprocess_flag = self.preprocess_checkbox.isChecked()
        bg_removal_flag = self.bg_removal_checkbox.isChecked()
        custom_filter_flag = self.lab_color_checkbox.isChecked()

        resize_dim = self.resize_slider.value()

        # Build color_key_array based on user selections
        color_key_array = []

        # Collect selected default colors
        for color in self.default_color_key_array:
            color_number = color['number']
            enable_checkbox = self.color_checkboxes[color_number]
            if enable_checkbox.isChecked():
                # Add a copy of the color to the array
                color_key_array.append(color.copy())

        # Determine which color (if any) is marked as RGB
        rgb_color_number = None
        for color_number, rgb_checkbox in self.rgb_checkboxes.items():
            if rgb_checkbox.isChecked():
                rgb_color_number = color_number
                break

        # If an RGB color is selected, replace its number with 5
        if rgb_color_number is not None:
            for color in color_key_array:
                if color['number'] == rgb_color_number:
                    color['number'] = 5
                    break

        # Determine which color (if any) is marked as Blank
        blank_color_num = None
        for color_number, blank_checkbox in self.blank_checkboxes.items():
            if blank_checkbox.isChecked():
                blank_color_num = color_number
                break

        # If a Blank color is selected, replace its number with -1
        if blank_color_num is not None:
            for color in color_key_array:
                if color['number'] == blank_color_num:
                    color['number'] = -1
                    break

        # Update each color in the array with its corresponding slider values
        for color in color_key_array:
            color_number = color['number']
            
            # Skip RGB (5) and Blank (-1) as they don't need these values
            if color_number in [5, -1]:
                continue

            # Get the slider values for Boost and Threshold
            boost_slider = self.boost_sliders.get(color_number)
            threshold_slider = self.threshold_sliders.get(color_number)
            
            if boost_slider is not None:
                color['boost'] = boost_slider.value() / 10.0  # Convert slider value to float (e.g., 1.2)
            else:
                color['boost'] = 1.4  # Default value if slider not found

            if threshold_slider is not None:
                color['threshold'] = threshold_slider.value()
            else:
                color['threshold'] = 20  # Default threshold if slider not found

        process_mode = self.processing_combobox.currentText()

        # Collect parameters from parameter widgets
        process_params = {}
        processing_function = processing_method_registry.get(process_mode)
        if not processing_function:
            QMessageBox.warning(self, "Error", "Invalid processing method selected.")
            return
        default_params = getattr(processing_function, 'default_params', {})
        for param_name, default_value in default_params.items():
            widget = self.parameter_widgets.get(param_name)
            if isinstance(widget, QSlider):
                if isinstance(default_value, float):
                    value = widget.value() / 100.0
                else:
                    value = widget.value()
                process_params[param_name] = value
            elif isinstance(widget, QCheckBox):
                process_params[param_name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                process_params[param_name] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                text = widget.text()
                if isinstance(default_value, int):
                    try:
                        process_params[param_name] = int(text)
                    except ValueError:
                        process_params[param_name] = default_value
                elif isinstance(default_value, float):
                    try:
                        process_params[param_name] = float(text)
                    except ValueError:
                        process_params[param_name] = default_value
                else:
                    process_params[param_name] = text
            else:
                process_params[param_name] = default_value
        brightness = self.brightness_slider.value() / 100
        # Prepare parameters for image processing

        params = {
            'image_path': self.image_path,
            'remove_bg' : bg_removal_flag,
            'preprocess_flag': preprocess_flag,
            'use_lab' : custom_filter_flag,
            'brightness' : brightness,
            'resize_dim': resize_dim,
            'color_key_array': color_key_array,
            'process_mode': process_mode,
            'process_params': process_params
        }
        # Switch to status and progress view
        self.status_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.action_layout.setCurrentIndex(1) 
        self.status_label.setText("Starting processing...")
        self.progress_bar.setValue(0)


        # Start image processing in a separate thread
        self.signals = WorkerSignals()
        self.signals.progress.connect(self.update_progress)
        self.signals.message.connect(self.update_status)
        self.signals.error.connect(self.show_error)
        self.processing_thread = ImageProcessingThread(params, self.signals)
        self.processing_thread.start()
        self.monitor_thread()

    def monitor_thread(self):
        """
        Monitors the processing thread and updates the UI upon completion.
        """
        if self.processing_thread.is_alive():
            QTimer.singleShot(100, self.monitor_thread)
            return

        self.progress_bar.setValue(100)
        self.status_label.setText("Processing complete!")

        # Paths for PNG and GIF previews
        preview_png_path = exe_path_fs('game_data/stamp_preview/preview.png')
        preview_gif_path = exe_path_fs('game_data/stamp_preview/preview.gif')

        try:
            if self.is_gif:
                print(preview_gif_path)
                self.process_and_display_gif(preview_gif_path)
            elif preview_png_path.exists():
                self.handle_png(preview_png_path)
            else:
                raise FileNotFoundError("Processed image not found.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to display result: {str(e)}")
            self.reset_ui_after_failure()
            return


        self.stacked_widget.setCurrentWidget(self.result_widget)
        self.canpaste = True
        # Re-enable the Process button if needed
        self.reset_ui_after_processing()

    def reset_ui_after_failure(self):
        """
        Resets the UI after a processing failure.
        """
        self.action_layout.setCurrentIndex(0)  # Switch back to process button
        self.status_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Status: Ready")

        # Re-enable the Process button
        self.process_button.setVisible(True)
        self.process_button.setEnabled(True)

        # Show back and refresh buttons
        if self.back_button:
            self.back_button.show()
        if self.refresh_button:
            self.refresh_button.show()
            
    def process_and_display_gif(self, gif_path):
        """
        Processes an input GIF, resizes frames with hard pixel edges (NEAREST),
        and displays the animation on a QLabel using QTimer for manual frame handling.
        """
        try:
            with Image.open(gif_path) as img:
                if not img.is_animated:
                    raise ValueError("Input file is not an animated GIF.")

                frames = []
                durations = []

                # QLabel dimensions
                max_width, max_height = 600, 530

                # Process each frame
                for frame in ImageSequence.Iterator(img):
                    frame = frame.convert("RGBA")  # Preserve transparency
                    scale_factor = min(max_width / frame.width, max_height / frame.height)
                    new_width = int(frame.width * scale_factor)
                    new_height = int(frame.height * scale_factor)

                    # Resize the frame while preserving aspect ratio
                    resized_frame = frame.resize((new_width, new_height), Image.Resampling.NEAREST)

                    # Create a transparent canvas
                    canvas = Image.new("RGBA", (max_width, max_height), (0, 0, 0, 0))
                    # Center the resized frame on the canvas
                    offset_x = (max_width - new_width) // 2
                    offset_y = (max_height - new_height) // 2
                    canvas.paste(resized_frame, (offset_x, offset_y), resized_frame)

                    # Convert to QPixmap
                    data = canvas.tobytes("raw", "RGBA")
                    qimage = QImage(data, canvas.width, canvas.height, QImage.Format_RGBA8888)
                    pixmap = QPixmap.fromImage(qimage)

                    # Store the QPixmap and duration
                    frames.append(pixmap)
                    durations.append(frame.info.get("duration", 100))  # Default to 100ms if no duration

                if frames:
                    # Set up frame animation with QTimer
                    self.current_frame = 0
                    self.timer = QTimer(self)
                    self.timer.timeout.connect(lambda: self.update_gif_frame2(frames, durations))
                    self.timer.start(durations[0])  # Start with the first frame's duration
                    self.gif_frames = frames
                    self.gif_durations = durations

                    # Display the first frame to initialize the QLabel
                    self.result_image_label.setPixmap(frames[0])
                    self.result_image_label.setAlignment(Qt.AlignCenter)

        except Exception as e:
            print(f"Error processing or displaying GIF: {e}")
            QMessageBox.warning(self, "Error", f"Failed to process and display GIF: {e}")

    def update_gif_frame2(self, frames, durations):
        """
        Updates the QLabel with the next frame in the animation sequence.
        """
        # Update QLabel with the current frame
        self.result_image_label.setPixmap(frames[self.current_frame])

        # Increment the frame index
        self.current_frame = (self.current_frame + 1) % len(frames)

        # Update timer interval for the next frame
        next_duration = durations[self.current_frame]
        self.timer.start(next_duration)



    def handle_png(self, png_path):
        """
        Resizes and displays a PNG in QLabel, preserving aspect ratio without blank space.
        """
        if not os.path.exists(png_path):
            raise FileNotFoundError(f"PNG file not found: {png_path}")

        try:
            with Image.open(png_path).convert("RGBA") as img:


                # QLabel dimensions
                max_width, max_height = 600, 530
                scale_factor = min(max_width / img.width, max_height / img.height)
                new_width = int(img.width * scale_factor)
                new_height = int(img.height * scale_factor)

                # Resize with high-quality scaling
                resized_img = img.resize((new_width, new_height), Image.Resampling.NEAREST)

                # Convert to QPixmap and display
                qimage = ImageQt.ImageQt(resized_img)
                pixmap = QPixmap.fromImage(qimage)
                self.result_image_label.setPixmap(pixmap)
                self.result_image_label.setAlignment(Qt.AlignCenter)
                self.result_image_label.setStyleSheet("background-color: transparent; border: none;")

        except Exception as e:
            print(f"Error displaying PNG: {e}")
            QMessageBox.warning(self, "Error", f"Failed to display PNG: {e}")


    def reset_ui_after_processing(self):
        """
        Resets the UI to the initial state after processing.
        Ensures all animations and temporary data are cleared.
        """
        self.action_layout.setCurrentIndex(0)
        self.status_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Status: Ready")


        if self.back_button:
            self.back_button.show()
        if self.refresh_button:
            self.refresh_button.show()

            
    def update_progress(self, progress):
        QTimer.singleShot(0, lambda: self.progress_bar.setValue(progress))

    def update_status(self, message):
        QTimer.singleShot(0, lambda: self.status_label.setText(message))

    def show_error(self, message):
        QMessageBox.warning(self, "Error", message)
        self.process_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Error occurred during processing.")
    

    def reset_to_initial_state(self):
        """
        Resets the application state when the user navigates back to the initial menu.
        """
        # Reset image and GIF-related states
        self.image_path = None
        self.image = None
        self.is_gif = False
        self.movie = None

        # Clear image label
        self.image_label.clear()
        self.image_label.setText("Oopsies")
        self.image_label.setStyleSheet("")

        # Reset resize slider to default
        self.resize_slider.setValue(128)
        self.resize_slider.setMaximum(400)

        # Reset color options and related UI elements
        self.reset_color_options()

        # Reset processing flags
        self.preprocess_checkbox.setChecked(True)
        self.lab_color_checkbox.setChecked(False)
        self.bg_removal_checkbox.setChecked(False)

        # Ensure all boost and threshold elements are hidden
        for color_number in self.boost_labels:
            self.boost_labels[color_number].setVisible(False)
            self.boost_sliders[color_number].setVisible(False)
            self.threshold_labels[color_number].setVisible(False)
            self.threshold_sliders[color_number].setVisible(False)

        # Show back and refresh buttons
        if self.back_button:
            self.back_button.show()
        if self.refresh_button:
            self.refresh_button.show()

        # Reset status and progress indicators
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Status: Ready")
        self.status_label.setVisible(False)

        # Re-enable the Process button
        self.process_button.setVisible(True)
        self.process_button.setEnabled(True)

        # Reset any additional flags
        self.canpaste = True
        self.delete_mode = False
        if hasattr(self, 'timer') and self.timer is not None:
            self.timer.stop()
            self.timer = None

        # Clear the QLabel
        if hasattr(self, 'result_image_label') and self.result_image_label is not None:
            self.result_image_label.clear()

        # Reset the animation-related attributes
        self.gif_frames = None
        self.gif_durations = None
        self.current_frame = None


    def go_to_initial_menu(self, usepreview = False):
        """
        Handles navigation back to the initial menu and resets the application state.
        """
        if usepreview:
            self.display_new_stamp()
        else: 
            self.background_label.setPixmap(self.load_and_display_random_image())

        if not hasattr(self, 'secondary_widget'):
            self.setup_secondary_menu()
        
        self.reset_to_initial_state()
        self.reset_color_options()
        self.stacked_widget.setCurrentIndex(0)  # Switch to the initial menu
        self.canpaste = True
    
    # Define callback function for feedback
    def callback(self, message, center=False):
        """Handles user feedback."""
        print(message)
        if not self.last_message_displayed:
            self.show_floating_message(message, center)
            self.last_message_displayed = message

    def randomize_saved_stamps(self):
        """
        Randomizes the order of entries in the saved_stamps.json file.
        """
        # Use the new AppData directory
        appdata_dir = get_appdata_dir()
        saved_stamps_json = appdata_dir / "saved_stamps.json"

        # Check if JSON file exists
        if not saved_stamps_json.exists():
            return

        # Load JSON entries
        try:
            with open(saved_stamps_json, 'r') as f:
                saved_stamps = json.load(f)
        except Exception as e:
            self.callback("Error loading JSON file")
            return

        # Randomize entries
        randomized_entries = list(saved_stamps.items())
        random.shuffle(randomized_entries)

        # Convert back to dictionary and save
        randomized_dict = dict(randomized_entries)
        with open(saved_stamps_json, 'w') as f:
            json.dump(randomized_dict, f, indent=4)

        self.show_floating_message("Randomized!", True)
        self.repopulate_grid()


# Function to compute hash for a file
    def compute_hash(self, file_path):
        import hashlib
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            self.callback(f"Error: {e}")
            return None

    # Save Current Function
    def save_current(self, center=False):
        # Reset the last message at the beginning of the operation
        self.last_message_displayed = None

        # Define paths
        appdata_dir = get_appdata_dir()
        current_stamp_dir = exe_path_fs("game_data/current_stamp_data")
        preview_dir = exe_path_fs("game_data/stamp_preview")
        saved_stamps_json = appdata_dir / "saved_stamps.json"
        saved_stamps_dir = appdata_dir / "saved_stamps"

        stamp_path = current_stamp_dir / "stamp.txt"
        frames_path = current_stamp_dir / "frames.txt"


        # Step 1: Read and parse stamp.txt
        if not stamp_path.exists():
            self.callback("Error: stamp.txt not found")
            return
        try:
            with open(stamp_path, 'r') as f:
                first_line = f.readline().strip()
            parts = first_line.split(',')
            is_gif_flag = parts[2]  # Extract the flag at the third position
        except Exception as e:
            self.callback(f"Error: {e}")
            return

        if is_gif_flag not in ["img", "gif"]:
            self.callback("Error: Invalid flag in stamp.txt")
            return

        # Step 2: Compute hash for stamp.txt
        stamp_hash = self.compute_hash(stamp_path)
        if not stamp_hash:
            return

        # Step 3: Load or initialize saved_stamps.json
        appdata_dir.mkdir(parents=True, exist_ok=True)  # Ensure GUI directory exists
        if not saved_stamps_json.exists():
            saved_stamps = {}
        else:
            try:
                with open(saved_stamps_json, 'r') as f:
                    saved_stamps = json.load(f)
            except Exception:
                saved_stamps = {}

        # Step 4: Check if hash already exists
        if stamp_hash in saved_stamps:
            self.callback("already saved dummy", center)
            return
        
        # Step 5: Check for preview file
        preview_path = preview_dir / ("preview.png" if is_gif_flag == "img" else "preview.gif")
        if not preview_path.exists():
            self.callback(f"Error: {preview_path.name} not found")
            return


        # Step 6: Create folder for the new stamp
        stamp_folder = saved_stamps_dir / stamp_hash
        stamp_folder.mkdir(parents=True, exist_ok=True)

        # Step 7: Copy relevant files to the new folder
        try:
            (stamp_folder / "stamp.txt").write_bytes(stamp_path.read_bytes())
            if is_gif_flag == "gif":
                (stamp_folder / "frames.txt").write_bytes(frames_path.read_bytes())
        except Exception as e:
            self.callback(f"Error: {e}")
            return

        # Step 8: Call the `get_preview` function
        try:
            self.get_preview(preview_path, stamp_folder)
        except Exception as e:
            self.callback(f"Error: {e}")
            return

        # Step 9: Update saved_stamps.json
        saved_stamps[stamp_hash] = {"is_gif": (is_gif_flag == "gif")}
        with open(saved_stamps_json, 'w') as f:
            json.dump(saved_stamps, f, indent=4)

        self.callback("saved gif" if is_gif_flag == "gif" else "saved image", center)
        if center:
            self.repopulate_grid()
        
    def get_preview(self, preview_path, target_folder):
        """
        Process preview images (PNG or GIF), resize them to fit within 128x128 without warping,
        align them to the center, and save as WebP format.
        """
        target_file = target_folder / "preview.webp"
        output_size = (128, 128)

        def resize_and_pad_image(img):
            # Calculate aspect ratio to fit within 128x128
            img_ratio = img.width / img.height
            box_ratio = output_size[0] / output_size[1]

            if img_ratio > box_ratio:
                # Image is wider, fit by width
                new_width = output_size[0]
                new_height = int(output_size[0] / img_ratio)
            else:
                # Image is taller or square, fit by height
                new_height = output_size[1]
                new_width = int(output_size[1] * img_ratio)

            # Resize image while maintaining aspect ratio
            img = img.resize((new_width, new_height), Image.NEAREST)

            # Create a transparent canvas
            canvas = Image.new("RGBA", output_size, (0, 0, 0, 0))

            # Calculate position to center the image
            offset_x = (output_size[0] - new_width) // 2
            offset_y = (output_size[1] - new_height) // 2

            # Paste resized image onto the canvas
            canvas.paste(img, (offset_x, offset_y), img)
            return canvas

        if preview_path.suffix == ".png":
            img = Image.open(preview_path).convert("RGBA")
            resized_img = resize_and_pad_image(img)
            resized_img.save(target_file, format="WEBP", lossless=True)
        elif preview_path.suffix == ".gif":
            original_gif = Image.open(preview_path)
            frames = []
            durations = []

            # Process each frame
            for frame in ImageSequence.Iterator(original_gif):
                durations.append(frame.info.get("duration", 100))  # Default to 100 ms if no duration info
                frames.append(resize_and_pad_image(frame.convert("RGBA")))

            # Save the resized GIF with the original durations
            frames[0].save(
                target_file,
                save_all=True,
                append_images=frames[1:],
                loop=original_gif.info.get("loop", 0),
                duration=durations,
                format="WEBP",
                lossless=True,
            )
        else:
            raise FileNotFoundError("Invalid preview format")

def initialize_saved():
    """
    Initialize the saved stamps directory by cloning data from
    `exe_data/saved_stamp_initial` into the AppData directory.
    If the directory already exists, it is cleared before cloning.
    """
    appdata_dir = get_appdata_dir()
    
    # Paths for AppData directories
    saved_stamps_dir = appdata_dir / "saved_stamps"
    saved_stamps_json = appdata_dir / "saved_stamps.json"

    # Clear the existing AppData directory
    if saved_stamps_dir.exists():
        shutil.rmtree(saved_stamps_dir)
    if saved_stamps_json.exists():
        saved_stamps_json.unlink()

    # Path to the initial data directory
    initial_dir = exe_path_fs("exe_data/saved_stamp_initial")

    # Copy contents of the initial directory to the AppData directory
    for item in initial_dir.iterdir():
        if item.is_dir():
            # Copy the saved_stamps directory
            shutil.copytree(item, appdata_dir / item.name)
        elif item.is_file():
            # Copy the saved_stamps.json file
            shutil.copy(item, appdata_dir / item.name)

    print(f"Initialized saved stamps directory in: {appdata_dir}")



def cleanup_saved_stamps():
    """
    Validate and clean up the saved stamps directory and associated JSON file.
    Calls `initialize_saved` if the AppData directory or the saved stamps directory is missing.
    """
    appdata_dir = get_appdata_dir()
    saved_stamps_dir = appdata_dir / "saved_stamps"
    saved_stamps_json = appdata_dir / "saved_stamps.json"

    validated_entries = []  # List to track validated files
    reconstructed_entries = []  # Entries reconstructed from directory
    removed_folders = []  # List to track removed folders

    # If the saved stamps directory is missing, initialize it
    if not saved_stamps_dir.exists():
        print("Saved stamps directory not found. Initializing...")
        initialize_saved()
        return

    # Validate the .json file
    saved_stamps = {}
    json_valid = False  # Track if the JSON is valid
    if saved_stamps_json.exists():
        try:
            with open(saved_stamps_json, 'r') as f:
                saved_stamps = json.load(f)
                if not isinstance(saved_stamps, dict):
                    raise ValueError("JSON is not a dictionary.")
            json_valid = True
        except Exception as e:
            print(f"Corrupted JSON file detected: {e}. Attempting reconstruction.")
    else:
        print("JSON file not found. Attempting reconstruction.")

    # Collect folders in the saved_stamps directory
    actual_folders = {folder.name for folder in saved_stamps_dir.iterdir() if folder.is_dir()}

    if not json_valid:
        # JSON is missing or invalid: Reconstruct it from the existing folders
        print("Reconstructing JSON entries...")
        for folder_name in actual_folders:
            folder_path = saved_stamps_dir / folder_name
            stamp_txt_path = folder_path / "stamp.txt"
            preview_webp_path = folder_path / "preview.webp"

            if stamp_txt_path.exists() and preview_webp_path.exists():
                # Check for "frames.txt" to determine if it is a GIF
                is_gif = (folder_path / "frames.txt").exists()
                saved_stamps[folder_name] = {"is_gif": is_gif}
                reconstructed_entries.append(folder_name)
            else:
                # Remove invalid folders
                removed_folders.append(folder_name)
                for file in folder_path.iterdir():
                    file.unlink()  # Remove files in the folder
                folder_path.rmdir()  # Remove the folder itself

        # Save the reconstructed JSON
        with open(saved_stamps_json, 'w') as f:
            json.dump(saved_stamps, f, indent=4)
        print("Reconstructed JSON saved.")
    else:
        # JSON is valid: Delete all folders not listed in the JSON
        print("Deleting folders not listed in JSON...")
        valid_hashes = set(saved_stamps.keys())
        for folder_name in actual_folders - valid_hashes:
            folder_path = saved_stamps_dir / folder_name
            removed_folders.append(folder_name)
            for file in folder_path.iterdir():
                file.unlink()  # Remove files in the folder
            folder_path.rmdir()  # Remove the folder itself

        # Validate and clean folders listed in JSON
        for folder_name in valid_hashes:
            folder_path = saved_stamps_dir / folder_name
            if not folder_path.exists():
                continue  # Skip non-existent folders

            # Ensure required files exist
            stamp_txt_path = folder_path / "stamp.txt"
            preview_webp_path = folder_path / "preview.webp"
            if stamp_txt_path.exists() and preview_webp_path.exists():
                validated_entries.append(folder_name)
            else:
                # Remove invalid folders
                removed_folders.append(folder_name)
                for file in folder_path.iterdir():
                    file.unlink()  # Remove files in the folder
                folder_path.rmdir()  # Remove the folder itself
                # Remove from JSON
                saved_stamps.pop(folder_name, None)

        # Save the updated JSON
        with open(saved_stamps_json, 'w') as f:
            json.dump(saved_stamps, f, indent=4)

    # Print results
    print("\nValidated Entries:")
    print(validated_entries if validated_entries else "No entries validated.")

    print("\nReconstructed Entries:")
    print(reconstructed_entries if reconstructed_entries else "No entries reconstructed.")

    print("\nRemoved Folders:")
    print(removed_folders if removed_folders else "No folders removed.")


if __name__ == '__main__':
    app = QApplication(sys.argv)

    # Set the Application User Model ID for Windows taskbar (prevents grouping issues)
    if sys.platform.startswith('win'):
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u"ImageProcessingGUI")

    # Define the icon path and apply the icon
    icon_path = exe_path_stylesheet("exe_data/icon.png")

    app_icon = None
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)  # QIcon is safe here after QApplication is created
        app.setWindowIcon(app_icon)
    else:
        print("Warning: icon.png not found in directory.")

    # Create and show the main window
    window = MainWindow()
    if app_icon:
        window.setWindowIcon(app_icon)  # Ensure the window gets the icon
    window.show()

    # Start the event loop
    sys.exit(app.exec())