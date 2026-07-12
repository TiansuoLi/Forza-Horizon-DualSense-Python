"""Forza Horizon-aware LED lighting logic."""
import time

class LightingController:
    def __init__(self, settings):
        self.s = settings
        self.last_update = 0.0
        self.flash_state = False
        self.smoothed_rpm_ratio = 0.0  # 新增：平滑转速比

    def update(self, t, now, dsx_mode=False):
        """返回 (r, g, b), player_led_value, mic_state 或 None
        dsx_mode=True 时，player_led_value 为 DSX layout (1,3,5)
        dsx_mode=False 时，player_led_value 为 HID bitmask (0-31)
        """

        if not self.s.enable_lighting:
            if now - self.last_update < 0.5: 
                return None
            self.last_update = now
            self.smoothed_rpm_ratio = 0.0 
            return (0, 0, 0), (5 if dsx_mode else 0), 2

        # 2. 0.1s 节流
        if now - self.last_update < 0.1:
            return None
            
        dt = now - self.last_update  # 实际时间间隔
        self.last_update = now
        self.flash_state = not self.flash_state
        
        # 3. 计算转速比
        current_rpm = t.get("rpm", t.get("CurrentEngineRpm", 0))
        max_rpm = t.get("max_rpm", t.get("EngineMaxRpm", 8000))
        idle_rpm = t.get("idle_rpm", t.get("EngineIdleRpm", 0))
        
        if max_rpm > idle_rpm:
            rpm_ratio = (current_rpm - idle_rpm) / (max_rpm - idle_rpm)
        else:
            rpm_ratio = 0.0
        rpm_ratio = max(0.0, min(1.0, rpm_ratio))
        
        # === Smoothing Algorithm ===
        # Rising:  Immediate tracking (higher coefficient for fast response)
        # Falling: Gradual decay    (lower coefficient to sustain flash state)
        #
        # Parameters:
        #   rise_rate  = Rise speed  (proportion caught up per second); set higher for responsive lights
        #   decay_rate = Decay speed (proportion decayed per second);  set lower to maintain flash effect
        rise_rate = 20.0
        decay_rate = 2.6
        
        rate = rise_rate if rpm_ratio >= self.smoothed_rpm_ratio else decay_rate
        alpha = 1.0 - (2.71828 ** (-rate * dt))
        self.smoothed_rpm_ratio += (rpm_ratio - self.smoothed_rpm_ratio) * alpha
        self.smoothed_rpm_ratio = max(0.0, min(1.0, self.smoothed_rpm_ratio))
        
        # 用平滑后的值替代原始 rpm_ratio 进行后续判断
        display_ratio = self.smoothed_rpm_ratio
        
        # 4. 动态阈值计算
        green_t = self.s.light_green_pct
        orange_t = self.s.light_orange_pct
        red_t = self.s.light_red_pct
        flash_t = self.s.light_flash_pct

        # === RGB LightBar ===
        if current_rpm == 0.0:
            r, g, b = 30, 0, 163
        elif display_ratio < green_t:
            r, g, b = 0, 240, 0
        elif display_ratio < orange_t:
            r, g, b = 255, 130, 0
        elif display_ratio < flash_t:
            r, g, b = 255, 0, 0
        else:
            r, g, b = (255, 0, 0) if self.flash_state else (0, 0, 160)
            
        # === Player LED (区分模式) ===
        if dsx_mode:
            if display_ratio >= flash_t:
                player_led_val = 3 if self.flash_state else 5
            elif display_ratio >= orange_t:
                player_led_val = 3
            elif display_ratio >= green_t:
                player_led_val = 1
            else:
                player_led_val = 5
        else:
            if current_rpm == 0.0:
                player_led_val = 0
            elif display_ratio < green_t:
                player_led_val = 4
            elif display_ratio < orange_t:
                player_led_val = 14
            elif display_ratio < flash_t:
                player_led_val = 31
            else:
                player_led_val = 31 if self.flash_state else 0  
                
        # === Mic LED ===
        if display_ratio < orange_t:
            mic_state = 2
        elif display_ratio < flash_t:
            mic_state = 0
        else:
            mic_state = 0 if self.flash_state else 2
            
        return (r, g, b), player_led_val, mic_state