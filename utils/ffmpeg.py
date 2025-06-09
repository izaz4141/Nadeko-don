import subprocess
import os

def combine_video_audio(video_path, audio_path, output_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file missing: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file missing: {audio_path}")

    success = False
    try:
        # First attempt: direct stream copy
        cmd_copy = [
            'ffmpeg',
            '-i', video_path,
            '-i', audio_path,
            '-c', 'copy',
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-y',
            output_path
        ]
        
        try:
            subprocess.run(cmd_copy, check=True, capture_output=True, text=True)
            success = True
        except subprocess.CalledProcessError as e:
            # Check for WebM audio codec error
            if output_path.lower().endswith('.webm'):
                # Second attempt: re-encode audio to Opus
                cmd_reencode = [
                    'ffmpeg',
                    '-i', video_path,
                    '-i', audio_path,
                    '-c:v', 'copy',
                    '-c:a', 'libopus',
                    '-b:a', '128k',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-y',
                    output_path
                ]
                try:
                    subprocess.run(cmd_reencode, check=True, capture_output=True, text=True)
                    success = True
                except subprocess.CalledProcessError as e2:
                    error_msg = f"FFmpeg failed during audio re-encoding ({e2.returncode}):\n{e2.stderr}"
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    print(error_msg)
            else:
                # Other errors during direct copy
                error_msg = f"FFmpeg failed during stream copy ({e.returncode}):\n{e.stderr}"
                if os.path.exists(output_path):
                    os.remove(output_path)
                print(error_msg)
    
    finally:
        # Only remove source files if combination succeeded
        if success:
            os.remove(video_path)
            os.remove(audio_path)
    
    return output_path if success else None
