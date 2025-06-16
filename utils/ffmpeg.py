import os, subprocess

def check_ffmpeg_in_path():
    """
    Checks if the 'ffmpeg' executable is available in the system's PATH.

    Returns:
        bool: True if 'ffmpeg' is found, False otherwise.
    """
    try:
        # Run a simple ffmpeg command to check its availability
        # We redirect stdout and stderr to DEVNULL to avoid printing output
        # and set check=True to raise CalledProcessError if ffmpeg is not found
        subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True, text=True)
        return True
    except FileNotFoundError:
        # This exception is raised if the 'ffmpeg' executable is not found in PATH
        print("Error: 'ffmpeg' executable not found in system PATH. Please install FFmpeg or add it to your PATH.")
        return False
    except subprocess.CalledProcessError as e:
        # This exception is raised if ffmpeg runs but exits with a non-zero status
        # (e.g., if there's a problem with the command itself, though '-version' is usually safe)
        print(f"Error checking FFmpeg version: {e.stderr}")
        return False
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred while checking FFmpeg: {e}")
        return False

def combine_video_audio(video_path, audio_path, output_path):
    """
    Combines a video file and an audio file into a new output file using FFmpeg.

    It first attempts a direct stream copy. If the output is WebM and the
    stream copy fails (often due to codec issues), it retries by re-encoding
    the audio to Opus.

    Args:
        video_path (str): The file path to the input video.
        audio_path (str): The file path to the input audio.
        output_path (str): The desired file path for the combined output.

    Returns:
        str or None: The path to the output file if successful, None otherwise.

    Raises:
        FileNotFoundError: If the video file, audio file, or ffmpeg executable is not found.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file missing: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file missing: {audio_path}")

    # Check if ffmpeg is available before proceeding
    if not check_ffmpeg_in_path():
        raise FileNotFoundError("FFmpeg is not found in your system PATH. Please ensure it's installed and accessible.")

    success = False
    try:
        # First attempt: direct stream copy
        # -i: input file
        # -c copy: stream copy (no re-encoding)
        # -map 0:v:0: map the first video stream from the first input
        # -map 1:a:0: map the first audio stream from the second input
        # -y: overwrite output file without asking
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
            # Execute the command
            subprocess.run(cmd_copy, check=True, capture_output=True, text=True)
            success = True
        except subprocess.CalledProcessError as e:
            # If direct copy fails, inspect the error
            raise Exception(f"Direct stream copy failed ({e.returncode}):\n{e.stderr}")
            # Check for specific WebM audio codec error, common with stream copy
            if output_path.lower().endswith('.webm') and "Codec not found or not supported" in e.stderr:
                print("Detected WebM output with potential audio codec issue, attempting audio re-encode to Opus...")
                # Second attempt: re-encode audio to Opus for WebM compatibility
                # -c:v copy: video stream copy
                # -c:a libopus: encode audio to Opus codec
                # -b:a 128k: audio bitrate of 128 kbps
                cmd_reencode = [
                    'ffmpeg',
                    '-i', video_path,
                    '-i', audio_path,
                    '-c:v', 'copy',
                    '-c:a', 'libopus',
                    '-b:a', '128k', # You can adjust this bitrate as needed
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-y',
                    output_path
                ]
                try:
                    subprocess.run(cmd_reencode, check=True, capture_output=True, text=True)
                    success = True
                    print(f"Successfully re-encoded audio and combined for: {output_path}")
                except subprocess.CalledProcessError as e2:
                    # Handle re-encoding failure
                    error_msg = f"FFmpeg failed during audio re-encoding ({e2.returncode}):\n{e2.stderr}"
                    if os.path.exists(output_path):
                        os.remove(output_path) # Clean up partially created file
                    raise Exception(error_msg)
            else:
                # Other errors during direct copy (not specifically WebM audio issue)
                error_msg = f"FFmpeg failed during stream copy ({e.returncode}):\n{e.stderr}"
                if os.path.exists(output_path):
                    os.remove(output_path) # Clean up partially created file
                raise Exception(error_msg)
    except Exception as ex:
        # Catch any other unexpected errors during the process
        raise Exception(f"An unexpected error occurred during video/audio combination: {ex}")
        if os.path.exists(output_path):
            os.remove(output_path) # Clean up partially created file
    
    return output_path if success else None