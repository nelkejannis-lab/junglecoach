"""Match-scoped cue timing for the local overlay."""


class CueGate:
    def __init__(self):
        self.active = None
        self.quiet_until = 0
        self.combat_until = 0
        self.last_health = None
        self.seen = set()

    def update(self, now, candidate, health=None, maximum=None, dead=False):
        if isinstance(health, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
            if self.last_health:
                time, hp = self.last_health
                if 0 <= now-time <= 2.5 and hp-health > maximum*.10:
                    self.combat_until = now+10
            self.last_health = (now, health)
        if dead:
            self.active = None
            return None
        if self.active and (self.active[0] <= now or (self.active[1] != 1 and now < self.combat_until)):
            self.active = None
        if candidate:
            priority, key, message, tone = candidate
            critical = priority == 1
            allowed = critical or (now >= self.quiet_until and now >= self.combat_until)
            if allowed and key not in self.seen and (not self.active or priority < self.active[1]):
                self.active = (now+7, priority, {"text": message, "tone": tone})
                self.seen.add(key)
                if not critical:
                    self.quiet_until = now+50
        return self.active[2] if self.active else None
