import asyncio
import smbus2
import picar
from picar import front_wheels, back_wheels, Servo
import math 
from asyncio_mqtt import Client, MqttError
import time
import random 
import logging

# ======================================== CONFIG ===========================================

DEBUG = True

LIDAR_I2C_ADDR = 0x62 # adress I2C lidar 
ACQ_COMMAND = 0x00 # reg lidar 
DISTANCE_REG_HI = 0x0f
DISTANCE_REG_LO = 0x10
LIDAR_FREQUENCY = 0.0011
CRIRICAL_DISTANCE = 50

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
picar.setup()

# ======================================== Classes ==========================================

class Lidar:
    def __init__(self, bus, freq):
        self.bus = bus
        self.frequency = freq

    async def get_distance(self):
        self.bus.write_byte_data(LIDAR_I2C_ADDR, ACQ_COMMAND, 0x04)
        await asyncio.sleep(self.frequency)

        dist_hi = self.bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_HI)
        dist_lo = self.bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_LO)
        distance = (dist_hi << 8) + dist_lo
        return distance
    
class MotorController:
    def __init__(self):
        self.fw = front_wheels.Front_Wheels()
        self.bw = back_wheels.Back_Wheels()
        self.sensorServo = Servo.Servo(1)

    async def stop(self):
        self.bw.stop()
        
    async def move(self, speed):
        self.bw.speed = abs(speed)
        if speed >= 0 and speed <= 100:
            self.bw.backward()
        elif speed < 0 and speed >= -100:
            self.bw.forward()

    async def fwheels_servo(self, direction):
        directions = {"left": self.fw.turn_left,
                      "mid": self.fw.turn_straight, 
                      "right": self.turn_right}
        
        if direction in directions: 
            directions[direction]()
            logging.info(f"Steering Angle: {direction}")

    async def servoLidar(self, arg):
        self.sensorServo.write(arg)

class MQTT:
    def __init__(self, queue):
        self.queue = queue

    async def listen(self):
        async with Client("test.mosquitto.org") as client:
            async with client.messages() as messages:
                await client.subscribe("BrianIsKool/test")
                async for message in messages:
                    await self.queue.put(dict(who="mqtt", cmd=message.payload.decode("utf-8")))

    async def publish(self, queue_data):
        async with Client("test.mosquitto.org") as client:
            while True:
                data = await queue_data.get()
                if data:
                    await client.publish("/BrianIsKool/stat", str(data))
                    logging.info(f"Published Data: {data}")
                await asyncio.sleep(0.1)
    

# ========================================= Logic =================================================
class Robot:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.lidar = Lidar(smbus2.SMBus(1), LIDAR_FREQUENCY)
        self.motors = MotorController()
        self.mqtt = MQTT(self.queue)

    async def lidar_task(self):
        while True:
            distance = await self.lidar.get_distance()
            await self.queue.put(dict(who="lidar", distance=distance))

    async def process_commands(self):
        while True:
            item = await self.queue.get()
            if item["who"] == "lidar":
                await self.handle_lidar_data(item["distance"])
            elif item["who"] == "mqtt":
                await self.handle_mqtt_command(item["cmd"])

    async def handle_lidar_data(self, distance):
        if distance < CRIRICAL_DISTANCE:
            await self.queue.put(dict(who="motor", speed=-30))
            await asyncio.sleep(1)
            await self.queue.put(dict(who="motor", speed=0))
            await self.queue.put(dict(who="servo", direction="right"))
            await asyncio.sleep(1)
            await self.queue.put(dict(who="servo", direction="mid"))
        else:
            await self.queue.put(dict(who="motor", speed=60))
            await self.queue.put(dict(who="servo", direction="mid"))

    async def handle_mqtt_command(self, cmd):
        commands = {
            "0": lambda: self.queue.put_nowait(dict(who="motor", speed=0)),
            "1": lambda: self.queue.put_nowait(dict(who="servo", direction="left")),
            "2": lambda: self.queue.put_nowait(dict(who="servo", direction="right")),
            "3": lambda: self.queue.put_nowait(dict(who="servo", direction="mid"))
        }
        if cmd in commands:
            await commands[cmd]()

    async def run(self):
        tasks = [
            asyncio.create_task(self.lidar_task()),
            asyncio.create_task(self.process_commands()),
            asyncio.create_task(self.mqtt.listen()),
            asyncio.create_task(self.mqtt.publish(self.queue))
        ]
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    robot = Robot()
    asyncio.run(robot.run())
