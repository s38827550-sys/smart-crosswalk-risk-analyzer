from src.detector.yolo_detector import detect_video

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, required=True)
    parser.add_argument("--no-log", action="store_true")
    args = parser.parse_args()

    detect_video(video_path=args.source, save_log=not args.no_log)