#!/usr/bin/env python3
"""
Download Kokoro-82M TTS Model

Usage:
    python scripts/download-kokoro-model.py
    python scripts/download-kokoro-model.py --output models/kokoro_models
    python scripts/download-kokoro-model.py --voices af_heart,af_bella,am_adam
    python scripts/download-kokoro-model.py --all-voices  # Download all available voices
    python scripts/download-kokoro-model.py --force  # Force re-download
"""

import os
import sys
import argparse
from pathlib import Path
from typing import List, Optional


class Colors:
    """ANSI color codes"""
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header():
    """Print script header"""
    print()
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}    Kokoro-82M TTS Model Downloader{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print()


def print_info(msg: str):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️  {msg}{Colors.END}")


def print_success(msg: str):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")


def print_warning(msg: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")


def print_error(msg: str):
    """Print error message"""
    print(f"{Colors.RED}❌ {msg}{Colors.END}")


def get_project_root() -> Path:
    """Get project root directory"""
    script_dir = Path(__file__).parent
    return script_dir.parent


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def check_dependencies():
    """Check if required dependencies are installed"""
    print_info("Checking dependencies...")
    
    missing_deps = []
    
    try:
        import torch
        print_success(f"PyTorch: {torch.__version__}")
    except ImportError:
        missing_deps.append("torch")
    
    try:
        import kokoro
        print_success("Kokoro: installed")
    except ImportError:
        missing_deps.append("kokoro")
    
    try:
        from huggingface_hub import hf_hub_download
        print_success("Hugging Face Hub: installed")
    except ImportError:
        missing_deps.append("huggingface-hub")
    
    if missing_deps:
        print()
        print_error("Missing dependencies:")
        for dep in missing_deps:
            print(f"  - {dep}")
        print()
        print_info("Install missing dependencies:")
        print(f"  pip install {' '.join(missing_deps)}")
        print()
        return False
    
    print()
    return True


def get_default_voices() -> List[str]:
    """Get default voice list"""
    return [
        'af_heart',    # American Female
        'af_bella',    # American Female
        'af_sarah',    # American Female
        'am_adam',     # American Male
        'am_michael',  # American Male
    ]


def get_all_voices() -> List[str]:
    """Get all available voices"""
    return [
        # American Female
        'af_heart', 'af_bella', 'af_sarah', 'af_nicole', 'af_sky',
        # American Male
        'am_adam', 'am_michael', 'am_liam',
        # British Female
        'bf_emma', 'bf_isabella',
        # British Male
        'bm_george', 'bm_lewis'
    ]


def download_model(model_dir: Path, force: bool = False) -> bool:
    """
    Download Kokoro model and config files
    
    Args:
        model_dir: Directory to save model
        force: Force re-download if files exist
    
    Returns:
        True if successful, False otherwise
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print_error("huggingface-hub not installed. Run: pip install huggingface-hub")
        return False
    
    print_info("Downloading Kokoro-82M model files...")
    print()
    
    repo_id = "hexgrad/Kokoro-82M"
    model_path = model_dir / "kokoro-v1_0.pth"
    config_path = model_dir / "config.json"
    
    # Create directory
    model_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Download model file
        if model_path.exists() and not force:
            size_mb = model_path.stat().st_size / (1024 * 1024)
            print_success(f"Model already exists: {model_path} ({size_mb:.1f} MB)")
        else:
            print_info(f"Downloading model: kokoro-v1_0.pth")
            hf_hub_download(
                repo_id=repo_id,
                filename="kokoro-v1_0.pth",
                local_dir=model_dir,
                local_dir_use_symlinks=False
            )
            size_mb = model_path.stat().st_size / (1024 * 1024)
            print_success(f"Model downloaded: {model_path} ({size_mb:.1f} MB)")
        
        # Download config file
        if config_path.exists() and not force:
            print_success(f"Config already exists: {config_path}")
        else:
            print_info(f"Downloading config: config.json")
            hf_hub_download(
                repo_id=repo_id,
                filename="config.json",
                local_dir=model_dir,
                local_dir_use_symlinks=False
            )
            print_success(f"Config downloaded: {config_path}")
        
        print()
        print_success("Model files downloaded successfully!")
        return True
        
    except Exception as e:
        print()
        print_error(f"Failed to download model: {e}")
        return False


def download_voices(model_dir: Path, voice_names: List[str], force: bool = False) -> bool:
    """
    Download voice files
    
    Args:
        model_dir: Directory to save voices
        voice_names: List of voice names to download
        force: Force re-download if files exist
    
    Returns:
        True if successful, False otherwise
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print_error("huggingface-hub not installed. Run: pip install huggingface-hub")
        return False
    
    print_info(f"Downloading {len(voice_names)} voice files...")
    print()
    
    repo_id = "hexgrad/Kokoro-82M"
    voices_dir = model_dir / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    
    success_count = 0
    
    for voice_name in voice_names:
        voice_file = f"{voice_name}.pt"
        voice_path = voices_dir / voice_file
        
        try:
            if voice_path.exists() and not force:
                size_mb = voice_path.stat().st_size / (1024 * 1024)
                print_success(f"Voice already exists: {voice_name} ({size_mb:.2f} MB)")
                success_count += 1
            else:
                print_info(f"Downloading voice: {voice_name}")
                hf_hub_download(
                    repo_id=repo_id,
                    filename=f"voices/{voice_file}",
                    local_dir=model_dir,
                    local_dir_use_symlinks=False
                )
                size_mb = voice_path.stat().st_size / (1024 * 1024)
                print_success(f"Voice downloaded: {voice_name} ({size_mb:.2f} MB)")
                success_count += 1
        except Exception as e:
            print_warning(f"Failed to download voice '{voice_name}': {e}")
    
    print()
    if success_count == len(voice_names):
        print_success(f"All {success_count} voices downloaded successfully!")
        return True
    elif success_count > 0:
        print_warning(f"Downloaded {success_count}/{len(voice_names)} voices")
        return True
    else:
        print_error("Failed to download any voices")
        return False


def list_voices(model_dir: Path):
    """List downloaded voices"""
    voices_dir = model_dir / "voices"
    
    print()
    print(f"{Colors.BOLD}Downloaded Voices:{Colors.END}")
    print("=" * 60)
    
    if not voices_dir.exists():
        print_warning("No voices directory found")
        return
    
    voices = sorted(voices_dir.glob("*.pt"))
    
    if not voices:
        print_warning("No voices downloaded yet")
        return
    
    for voice_path in voices:
        voice_name = voice_path.stem
        size_mb = voice_path.stat().st_size / (1024 * 1024)
        print(f"  {Colors.GREEN}✓{Colors.END} {voice_name:20s} ({size_mb:.2f} MB)")
    
    print()
    print(f"Total: {len(voices)} voices")


def print_model_info(model_dir: Path):
    """Print model information"""
    print()
    print(f"{Colors.BOLD}Model Information:{Colors.END}")
    print("=" * 60)
    print(f"Model directory: {model_dir.absolute()}")
    print(f"Repository: hexgrad/Kokoro-82M")
    print()
    
    model_path = model_dir / "kokoro-v1_0.pth"
    config_path = model_dir / "config.json"
    voices_dir = model_dir / "voices"
    
    if model_path.exists():
        size_mb = model_path.stat().st_size / (1024 * 1024)
        print(f"  {Colors.GREEN}✓{Colors.END} Model: kokoro-v1_0.pth ({size_mb:.1f} MB)")
    else:
        print(f"  {Colors.RED}✗{Colors.END} Model: Not downloaded")
    
    if config_path.exists():
        print(f"  {Colors.GREEN}✓{Colors.END} Config: config.json")
    else:
        print(f"  {Colors.RED}✗{Colors.END} Config: Not downloaded")
    
    if voices_dir.exists():
        voices = list(voices_dir.glob("*.pt"))
        print(f"  {Colors.GREEN}✓{Colors.END} Voices: {len(voices)} downloaded")
    else:
        print(f"  {Colors.RED}✗{Colors.END} Voices: 0 downloaded")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Download Kokoro-82M TTS Model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download model with default voices
  python scripts/download-kokoro-model.py
  
  # Download specific voices
  python scripts/download-kokoro-model.py --voices af_heart,af_bella,am_adam
  
  # Download all available voices
  python scripts/download-kokoro-model.py --all-voices
  
  # Force re-download
  python scripts/download-kokoro-model.py --force
  
  # Custom output directory
  python scripts/download-kokoro-model.py --output custom_models/kokoro
  
Available voices:
  American Female: af_heart, af_bella, af_sarah, af_nicole, af_sky
  American Male:   am_adam, am_michael, am_liam
  British Female:  bf_emma, bf_isabella
  British Male:    bm_george, bm_lewis

More voices: https://huggingface.co/hexgrad/Kokoro-82M/tree/main/voices
        """
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default='models/kokoro_models',
        help='Output directory for model (default: models/kokoro_models)'
    )
    
    parser.add_argument(
        '--voices', '-v',
        type=str,
        help='Comma-separated list of voice names to download (e.g., af_heart,am_adam)'
    )
    
    parser.add_argument(
        '--all-voices', '-a',
        action='store_true',
        help='Download all available voices'
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Force re-download even if files exist'
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='List downloaded voices'
    )
    
    parser.add_argument(
        '--info',
        action='store_true',
        help='Show model information'
    )
    
    args = parser.parse_args()
    
    # Print header
    print_header()
    
    # Get paths
    project_root = get_project_root()
    model_dir = project_root / args.output
    
    # Handle --list flag
    if args.list:
        list_voices(model_dir)
        return 0
    
    # Handle --info flag
    if args.info:
        print_model_info(model_dir)
        return 0
    
    # Check dependencies
    if not check_dependencies():
        return 1
    
    # Determine which voices to download
    if args.all_voices:
        voices = get_all_voices()
        print_info(f"Will download all {len(voices)} available voices")
    elif args.voices:
        voices = [v.strip() for v in args.voices.split(',')]
        print_info(f"Will download {len(voices)} specified voices")
    else:
        voices = get_default_voices()
        print_info(f"Will download {len(voices)} default voices")
        print_info("Use --all-voices to download all available voices")
    
    print()
    
    # Download model
    if not download_model(model_dir, force=args.force):
        return 1
    
    # Download voices
    if not download_voices(model_dir, voices, force=args.force):
        return 1
    
    # Print summary
    print()
    print("=" * 60)
    print_success("Download completed!")
    print()
    print_info("Model ready to use:")
    print(f"  Model directory: {model_dir.absolute()}")
    print(f"  Sample rate: 24000 Hz")
    print()
    print_info("Test the model:")
    print("  from kokoro import KPipeline")
    print("  pipeline = KPipeline(lang_code='a')")
    print("  audio = pipeline('Hello world!', voice='af_heart', speed=1.0)")
    print()
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print_warning("Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        print()
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
