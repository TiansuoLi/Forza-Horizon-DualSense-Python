"""Dynamic Rev Limiter (Redline) Detector for Forza Horizon."""
import logging
from typing import Optional, List

log = logging.getLogger(__name__)

def predict_rev_limiter(max_rpm: float) -> float:
    """
    Predicts the rev limiter (redline) based on the dashboard max_rpm.
    Empirical formula: 5000rpm -> 85%, 10500rpm -> 97.2%
    """
    if max_rpm <= 0:
        return 0.0
    
    # Linear interpolation for the ratio
    ratio = 0.85 + (max_rpm - 5000.0) * (0.972 - 0.85) / (10500.0 - 5000.0)
    
    # Clamp ratio to reasonable bounds (80% ~ 98%)
    ratio = max(0.80, min(0.98, ratio))
    
    return max_rpm * ratio

class RedlineDetector:
    """
    Dynamically detects the true rev limiter (redline) using game telemetry data.
    """
    def __init__(self):
        self._last_car_ordinal: int = -1
        self._last_car_pi: int = -1  # Track Performance Index for tuning changes
        
        self._prev_gear: int = 0
        self._prev_rpm: float = 0.0
        
        # State machine
        self._is_armed: bool = False
        self._peak_rpm: float = 0.0
        
        # Clustering data
        self._candidates: List[float] = []
        self._locked_redline: Optional[float] = None
        
    def reset(self, debug_print: bool):
        """Resets the detector state (usually called on car change or tuning upgrade)."""
        self._is_armed = False
        self._peak_rpm = 0.0
        self._candidates.clear()
        self._locked_redline = None
        if debug_print:
            print("[RedlineDetector] State reset, waiting for detection...")

    def update(self, t: dict, s=None) -> float:
        """
        Called every frame with the parsed telemetry dictionary `t` and settings `s`.
        Returns the redline value to use (locked true value, or predicted value).
        """
        # Read settings dynamically
        debug_print = getattr(s, "redline_debug_print", False) if s else False
        sample_count = getattr(s, "redline_sample_count", 4) if s else 4
        rpm_tolerance = getattr(s, "redline_rpm_tolerance", 400.0) if s else 400.0

        max_rpm = t.get("max_rpm", 0.0)
        cur_rpm = t.get("rpm", 0.0) 
        gear = t.get("gear", 0)
        accel = t.get("accel", 0) # 0-255
        
        # Get car identifiers
        car_ordinal = t.get("car_ordinal", -1)
        car_pi = t.get("car_performance_index", -1)
        
        # Extract speed and convert to km/h (game data is in m/s)
        speed_ms = t.get("speed", 0.0)
        speed_kmh = speed_ms * 3.6 
        
        # 1. Car/Tuning Change Detection
        # Only trigger if values are non-zero and actually change
        ordinal_changed = (car_ordinal != self._last_car_ordinal) and (car_ordinal != 0) and (car_ordinal != -1)
        pi_changed = (car_pi != self._last_car_pi) and (car_pi != 0) and (car_pi != -1)
        
        if ordinal_changed or pi_changed:
            if debug_print:
                print("\n[RedlineDetector] Car change or tuning upgrade detected!")
                if ordinal_changed:
                    print(f"    -> Car Ordinal changed: {self._last_car_ordinal} -> {car_ordinal}")
                if pi_changed:
                    print(f"    -> Performance Index changed: {self._last_car_pi} -> {car_pi}")
                print(f"[RedlineDetector] Dashboard Max RPM: {max_rpm:.0f}")
                
            self._last_car_ordinal = car_ordinal if car_ordinal != 0 else self._last_car_ordinal
            self._last_car_pi = car_pi if car_pi != 0 else self._last_car_pi
            
            self.reset(debug_print)
            
            # Print initial predicted value
            pred = predict_rev_limiter(max_rpm)
            if debug_print:
                print(f"[RedlineDetector] Initial predicted redline: {pred:.0f} RPM (used until true value is locked)\n")
            
        # 2. If already locked, return the locked value
        if self._locked_redline is not None:
            self._prev_gear = gear
            self._prev_rpm = cur_rpm
            return self._locked_redline

        # 3. State Machine Logic
        rpm_delta = cur_rpm - self._prev_rpm
        
        if not self._is_armed:
            # IDLE state: looking for conditions to arm
            # Condition: Gear >= 1 and heavy throttle (>= 230, approx 90%)
            if gear >= 1 and accel >= 230:
                self._is_armed = True
                self._peak_rpm = cur_rpm
                if debug_print:
                    print(f"[RedlineDetector] ARMED | Gear: {gear} | Speed: {speed_kmh:.1f} km/h | Throttle: {accel}")
        else:
            # ARMED state: tracking the peak
            if cur_rpm > self._peak_rpm:
                self._peak_rpm = cur_rpm
                
            # Check exit/record conditions
            record_candidate = False
            reason = ""
            
            # Case A: Upshift (gear > prev_gear)
            if gear > self._prev_gear and self._prev_gear >= 1:
                record_candidate = True
                reason = f"Upshift ({self._prev_gear} -> {gear})"
                
            # Case B: Downshift (gear < prev_gear) -> Filter rev-match spikes
            elif gear < self._prev_gear and self._prev_gear >= 1:
                if debug_print:
                    print(f"[RedlineDetector] Peak discarded | Reason: Downshift ({self._prev_gear} -> {gear}), likely rev-match")
                self._is_armed = False 
                
            # Case C: Sudden RPM drop (hitting rev limiter wall) while still on throttle
            elif rpm_delta < -400.0 and accel > 200:
                record_candidate = True
                reason = f"RPM drop ({rpm_delta:.0f}) while holding throttle"
                
            # Case D: Player lifts off throttle
            elif accel < 100:
                if debug_print:
                    print(f"[RedlineDetector] Peak discarded | Reason: Throttle lifted (Accel: {accel})")
                self._is_armed = False 

            # Record candidate and reset state
            if record_candidate and self._peak_rpm > 0:
                self._candidates.append(self._peak_rpm)
                self._is_armed = False
                
                if debug_print:
                    print(f"[RedlineDetector] Candidate recorded | Reason: {reason}")
                    print(f"    -> Peak RPM: {self._peak_rpm:.0f} | Gear: {self._prev_gear} -> {gear} | Speed: {speed_kmh:.1f} km/h | Throttle: {accel}")
                    print(f"    -> Current candidates: {[f'{v:.0f}' for v in self._candidates]}")
                
                # Check if clustering lock condition is met
                if len(self._candidates) >= sample_count:
                    self._try_lock(debug_print, sample_count, rpm_tolerance)

        # Update history
        self._prev_gear = gear
        self._prev_rpm = cur_rpm
        
        # 4. Return value: if not locked, return linear predicted value
        return predict_rev_limiter(max_rpm)

    def _try_lock(self, debug_print: bool, sample_count: int, rpm_tolerance: float):
        """Attempts to cluster candidates and lock the redline."""
        # Simple clustering: find the largest cluster within the RPM tolerance
        best_cluster = []
        
        for base_val in self._candidates:
            cluster = [v for v in self._candidates if abs(v - base_val) <= rpm_tolerance]
            if len(cluster) > len(best_cluster):
                best_cluster = cluster
                
        # Lock if the largest cluster contains at least `sample_count` values
        if len(best_cluster) >= sample_count:
            self._locked_redline = sum(best_cluster) / len(best_cluster)
            if debug_print:
                print(f"\n[RedlineDetector] REDLINE LOCKED!")
                print(f"    -> Final true redline: {self._locked_redline:.0f} RPM")
                print(f"    -> Clustered samples: {[f'{v:.0f}' for v in best_cluster]}")
                print(f"    -> This value will be used from now on.\n")
        else:
            if debug_print:
                print(f"[RedlineDetector] {sample_count} candidates reached, but variance is too high. Continuing collection...")