# Detailed Plan for UI Flow Fixes (Kiosk)
 
This document details the logic changes implemented in [mainpy.js](file:///t:/bamboo_nissin/static/js/mainpy.js) to optimize user experience, prevent accidental resets, and handle OCR/face capture errors.
 
---
 
## Issue 1: Infinite Face Capture Retry Loop (Until User Leaves)
 
### New Logic Analysis
Instead of resetting the system after 3 failed notifications, the system now maintains the face capture standby state until the person leaves the camera's view.
 
- **Loop Mechanism:** The `startFaceRetryTimer` function calls itself every 5 seconds.
- **Audio Restriction (Mitigation):** To avoid annoying the customer or surroundings, the `"The face is not clearly visible"` audio notification is limited to a maximum of **3 times**. From the 4th attempt onwards, the system continues to scan and log silently without audio.
- **Termination Conditions:**
    1. **Success:** Face captured → proceed to the next step.
    2. **User Left:** The `presenceStream` loop no longer detects a person in front of the camera → triggers `scheduleWelcomeIdleReturn` → after 30 seconds → `resetAll`.
 
### Specific Changes
- Removed the logic block that checks `faceRetryCount >= faceRetryMaxCount` to call `resetAll()`.
- Modified `startFaceRetryTimer` to include `if (state.faceRetryCount <= 3)` as a condition for playing the audio.
 
---
 
## Issue 2: Synchronizing Reset and "Thank You" Badge
 
### Problem
If a user pulls out the card very quickly immediately after a successful face capture, the card removal detection flow (`REMOVING`) might trigger `resetAll` instantly, potentially interrupting the "Thank You" badge display and background data saving steps.
 
### Solution
Synchronize states so that when `resetAll` is called, any pending timeouts or badge displays are cleared, ensuring no leftover UI elements appear after returning to the idle screen.
 
---
 
## Issue 3: Card Recognition Reset (Preventing Old Card Re-capture)
 
### Solution: "Block Reset if Card is Present"
Modified the `scheduleWelcomeIdleReturn` function to be "Card-aware" regarding physical card status.
 
1. **If person is gone for 30 seconds:** The system checks the `state.cardAutoDone` variable or `state.autoCyclePhase`.
2. **If a card is still present:**
    - Does NOT call `resetAll`.
    - Sets `autoCyclePhase` to `REMOVING`.
    - Displays the `"Please take your card"` overlay.
    - The system will only truly reset once the Cam1 detection loop confirms the card slot is empty.
3. **If the slot is empty:** Calls `resetAll` immediately.
 
**Benefit:** Completely prevents the system from automatically re-capturing a forgotten card from a previous user when the next person arrives.
 
---
 
## File Changes (Detailed)
 
### [MODIFY] [mainpy.js](file:///t:/bamboo_nissin/static/js/mainpy.js)
 
#### 1. Face Retry Logic (L388 & L415)
- Removed reset based on 3-try limit.
- Added restriction to audio guidance (max 3 times).
 
#### 2. Conditional Reset Logic (L235)
- Updated `scheduleWelcomeIdleReturn` to check card status before resetting.
 
#### 3. Cleanup Logic in resetAll (L2189)
- Ensured total cleanup of temporary session states.
 
---
 
## Verification Plan (QA)
 
| Scenario | Expected Behavior |
|---|---|
| Stand in front but hide face | System prompts 3 times, then waits silently until the person leaves. |
| Pull out card immediately after face photo | "Thank You" badge displays correctly without UI flickering or overlapping with the idle screen. |
| User forgets card and leaves | "Please take your card" overlay appears; system does not return to idle (eyes) screen automatically until card is removed, preventing accidental re-capture for the next user. |
