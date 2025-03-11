import asyncio
import smbus2
import picar
from picar import front_wheels, back_wheels, Servo
from asyncio_mqtt import Client
import os
import logging
import time

# ============================= CONFIG =============================
DEBUG = True
LIDAR_I2C_ADDR = 0x62  # I2C address of the LiDAR
ACQ_COMMAND = 0x00  # LiDAR acquisition command
DISTANCE_REG_HI = 0x0f  # High byte of distance register
DISTANCE_REG_LO = 0x10  # Low byte of distance register
LIDAR_FREQUENCY = 0.0011  # Frequency of LiDAR measurements
CRITICAL_DISTANCE = 50  # Critical distance for obstacle avoidance

# Initialize logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
picar.setup()

# Initialize hardware
fw = front_wheels.Front_Wheels()
bw = back_wheels.Back_Wheels()
sensor_servo = Servo.Servo(1)
bus = smbus2.SMBus(1)
time.sleep(1)  # Wait for hardware to initialize

# ============================= CLASSES =============================

class Lidar:
    def __init__(self, bus, frequency):
        self.bus = bus
        self.frequency = frequency

    async def get_distance(self):
        self.bus.write_byte_data(LIDAR_I2C_ADDR, ACQ_COMMAND, 0x04)
        await asyncio.sleep(self.frequency)
        dist_hi = self.bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_HI)
        dist_lo = self.bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_LO)
        return (dist_hi << 8) + dist_lo

class MotorController:
    def __init__(self, fw, bw, sensor_servo):
        self.fw = fw
        self.bw = bw
        self.sensor_servo = sensor_servo

    async def move_wheels(self, speed):
        self.bw.speed = abs(speed)
        if speed >= 0:
            self.bw.backward()
        else:
            self.bw.forward()

    async def steer_wheels(self, direction):
        if direction == "left":
            self.fw.turn_left()
        elif direction == "right":
            self.fw.turn_right()
        else:
            self.fw.turn_straight()
        if DEBUG:
            logging.info(f"Steering Angle: {direction}")

    async def move_servo(self, angle):
        self.sensor_servo.write(angle)

    async def stop(self):
        self.fw.turn_straight()
        self.bw.stop()

class MQTTClient:
    def __init__(self, queue):
        self.queue = queue

    async def listen(self):
        """Listen for MQTT messages."""
        async with Client("test.mosquitto.org") as client:
            async with client.messages() as messages:
                await client.subscribe("BrianIsKool/test")
                async for message in messages:
                    await self.queue.put({"who": "mqtt", "cmd": message.payload.decode("utf-8")})

    async def publish(self, queue_data):
        # """Publish LiDAR data to MQTT."""
        async with Client("test.mosquitto.org") as client:
            while True:
                data = await queue_data.get()
                if data:
                    await client.publish("/BrianIsKool/stat", str(data))
                    logging.info(f"Published Data: {data}")
                await asyncio.sleep(0.1)

# ============================= MAIN LOGIC =============================

async def lidar_task(lidar, queue_lidar):
    while True:
        distance = await lidar.get_distance()
        await queue_lidar.put({"who": "lidar", "distance": distance})

async def motor_task(motor_controller, queue_motor):
    while True:
        item = await queue_motor.get()
        if item["who"] == "wheels":
            await motor_controller.move_wheels(item["speed"])
        elif item["who"] == "servo_wheels":
            await motor_controller.steer_wheels(item["direction"])
        elif item["who"] == "servo_lidar":
            await motor_controller.move_servo(item["angle"])

async def lidar_data_task(queue_lidar, queue_motor, queue_lidar_data, motor_controller):
    offset = 0
    direction = 1
    data = {}

    while True:
        item = await queue_lidar.get()
        if item["who"] == "lidar":
            distance = item["distance"]
            data[offset] = distance

            # Update servo position
            offset += 4 * direction
            if offset >= 180:
                direction = -1
                offset = 180
            elif offset <= 0:
                direction = 1
                offset = 0

            await queue_motor.put({"who": "servo_lidar", "angle": offset})
            await queue_lidar_data.put(data)

        elif item["who"] == "mqtt":
            if item["cmd"] == "0":
                await queue_motor.put({"who": "wheels", "speed": 0})
                await motor_controller.stop()
                logging.info("PANIC STOP!!!!")
                os._exit(1)
                await queue_motor.put({"who": "wheels", "speed": 0})
            elif item["cmd"] in ["1", "2", "3"]:
                direction_map = {"1": "left", "2": "right", "3": "mid"}
                await queue_motor.put({"who": "servo_wheels", "direction": direction_map[item["cmd"]]})

async def main_task(queue_motor, queue_lidar_data):
    while True:
        data = await queue_lidar_data.get()
        min_distance = min(data.values())
        closest_angle = min(data, key=data.get)

        if all(dist < CRITICAL_DISTANCE for dist in data.values()):
            logging.info("A hopeless situation: we give it back!")
            await queue_motor.put({"who": "wheels", "speed": -30})
            await asyncio.sleep(1)
            await queue_motor.put({"who": "wheels", "speed": 0})
            await queue_motor.put({"who": "servo_wheels", "direction": "right"})
            await asyncio.sleep(1)
            await queue_motor.put({"who": "servo_wheels", "direction": "mid"})
        elif min_distance < CRITICAL_DISTANCE:
            if closest_angle < 90:
                logging.info("Obstacle on the right, turn left")
                await queue_motor.put({"who": "servo_wheels", "direction": "left"})
            else:
                logging.info("Obstacle on the left, turn right")
                await queue_motor.put({"who": "servo_wheels", "direction": "right"})
            await queue_motor.put({"who": "wheels", "speed": 30})
        else:
            logging.info("The path is clear, moving forward")
            await queue_motor.put({"who": "servo_wheels", "direction": "mid"})
            await queue_motor.put({"who": "wheels", "speed": 60})

async def run():

    queue_motor = asyncio.Queue()
    queue_lidar = asyncio.Queue()
    queue_lidar_data = asyncio.Queue()

    lidar = Lidar(bus, LIDAR_FREQUENCY)
    motor_controller = MotorController(fw, bw, sensor_servo)
    mqtt_client = MQTTClient(queue_lidar)

    tasks = [
        lidar_task(lidar, queue_lidar),
        motor_task(motor_controller, queue_motor),
        lidar_data_task(queue_lidar, queue_motor, queue_lidar_data, motor_controller=motor_controller),
        main_task(queue_motor, queue_lidar_data),
        mqtt_client.listen(),
        mqtt_client.publish(queue_lidar_data),
    ]

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(run())