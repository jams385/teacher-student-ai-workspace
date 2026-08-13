# Student Notice (Before Entering a Join Code)

*Shown once, before a student enters any join code — this is the student equivalent of "account creation," but lighter: no email or password, just a display name. Plain, short sentences — written for a student reading level, not a legal one. Pairs with a `profile_ack_at` timestamp on the session.*

---

## Before you join a class

You don't need an email or password to use this. You'll just pick a display name your teacher will see.

**What we collect here.**
- The display name you choose
- Nothing else — no email, no real name required, no account is created

**A few things to know.**
- Your display name is only used inside the class workspace you join — it's not a persistent account, and it's not shared outside your class.
- You can use a nickname instead of your real name if your teacher allows it — check with them first.
- Each time you join a workspace, you'll see a short notice specific to that class before you start chatting.

---

☐ **I understand.**

[ Continue ]

---
*Internal note: store `profile_ack_at = timezone.now()` on the in-progress session before the join-code step. No PII beyond the chosen display name should be collected here.*
