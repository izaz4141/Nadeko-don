import time

class ProgressTimer:
    """
    A timer class that can be paused, resumed, and integrated into progress tracking.
    It provides methods to start, pause, resume, stop, and get the elapsed time.
    """

    def __init__(self):
        """
        Initializes the timer with default values.
        - start_time: The timestamp when the timer was last started or resumed.
        - elapsed_time: The total time accumulated while the timer was running.
        - _state: The current state of the timer ('stopped', 'running', 'paused').
        """
        self.start_time = None
        self.elapsed_time = 0.0
        self._state = 'stopped' # Initial state

    def start(self):
        """
        Starts the timer. If the timer is already running, it does nothing.
        If it's paused, it acts like a resume. If it's stopped, it starts fresh.
        """
        if self._state == 'running':
            print("Timer is already running.")
            return
        elif self._state == 'paused':
            self.resume()
            return

        # If stopped, start fresh
        self.start_time = time.time()
        self.elapsed_time = 0.0  # Reset for a fresh start
        self._state = 'running'

    def pause(self):
        """
        Pauses the timer. If the timer is not running or already paused, it does nothing.
        When paused, the elapsed time is accumulated and the timer stops counting.
        """
        if self._state != 'running':
            print(f"Timer is {self._state}, cannot pause.")
            return

        # Calculate the time elapsed since the last start/resume and add it to total
        self.elapsed_time += time.time() - self.start_time
        self._state = 'paused'

    def resume(self):
        """
        Resumes the timer from its paused state. If the timer is not paused
        or already running, it does nothing.
        """
        if self._state != 'paused':
            print(f"Timer is {self._state}, cannot resume.")
            return

        self.start_time = time.time() # Set new start time for resumed counting
        self._state = 'running'

    def stop(self):
        """
        Stops the timer and resets all values.
        If the timer was running, its current elapsed time is finalized before reset.
        """
        if self._state == 'running':
            # Add remaining time if it was running
            self.elapsed_time += time.time() - self.start_time
        
        final_elapsed = self.elapsed_time # Store final value before resetting

        self._state = 'stopped'
        return final_elapsed

    def get_elapsedTime(self):
        """
        Returns the current elapsed time of the timer in seconds.
        If the timer is running, it includes the time since the last start/resume.
        """
        if self._state == 'running':
            return self.elapsed_time + (time.time() - self.start_time)
        elif self._state == 'paused':
            return self.elapsed_time
        else: # Timer is 'stopped'
            return self.elapsed_time # Will be 0.0 if just stopped or never started
