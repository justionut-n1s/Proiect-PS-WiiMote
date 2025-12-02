import socket
import json
import pyautogui  

UDP_IP = "0.0.0.0"
UDP_PORT = 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("Aștept date de la telefon...")

while True:
    data, addr = sock.recvfrom(1024)
    packet = json.loads(data)

    buttons = packet["buttons"]
    gyro = packet["gyro"]
    accel = packet["accel"]


    if buttons["A"]:
        pyautogui.keyDown('z') 
    else:
        pyautogui.keyUp('z')

    if buttons["B"]:
        pyautogui.keyDown('x') 
    else:
        pyautogui.keyUp('x')

 
    dpad = buttons["DPAD"]
    for key in ["up","down","left","right"]:
        pyautogui.keyUp(key)

    if dpad == "UP":
        pyautogui.keyDown("up")
    elif dpad == "DOWN":
        pyautogui.keyDown("down")
    elif dpad == "LEFT":
        pyautogui.keyDown("left")
    elif dpad == "RIGHT":
        pyautogui.keyDown("right")
