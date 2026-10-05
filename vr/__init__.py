"""
Phone VR mode: the phone in a cardboard viewer is the headset, this Mac is still the robot.

  head_input.py    HeadInput: the phone's orientation sensors -> where you look
  hand_input.py    HandInput: the phone's rear camera (MediaPipe, on the phone) -> your hand
  voice_input.py   VoiceInput: the phone's microphone -> Whisper -> ORCA, and ORCA's voice back
  teleop.py        TeleoperationController: combines them into what the robot should do
  calibration.py   the short sequence before the dive
  server.py        the https + WebSocket link to the phone
  session.py       ties the above to the game: draws both eyes, the headset HUD, the log
  web/             the page the phone opens

Experiment data goes through the same logger.py as everything else.
"""
