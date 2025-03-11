import asyncio
import smbus2
import picar
from picar import front_wheels, back_wheels, Servo
import math 
from asyncio_mqtt import Client, MqttError
import time
import random
import os


# Sorry for the shit code, if something is not clear, write to me in telegram - @BrianIsKool


# =============================GLOBAL===============================
DEBUG = True
picar.setup()

fw = front_wheels.Front_Wheels()
bw = back_wheels.Back_Wheels()
sensorServo = Servo.Servo(1)
LidarFrequency = 0.0011

LIDAR_I2C_ADDR = 0x62 # adress I2C lidar 
ACQ_COMMAND = 0x00 # reg lidar 
DISTANCE_REG_HI = 0x0f
DISTANCE_REG_LO = 0x10

bus = smbus2.SMBus(1)
time.sleep(1)
# ==================================================================

async def mqtt(queue): # das ist mqtt debug. 
    async with Client("test.mosquitto.org") as client:
        async with client.messages() as messages:
            await client.subscribe("BrianIsKool/test")
            async for message in messages:
                await queue.put(dict(who="mqtt", cmd=message.payload.decode("utf-8"))) 
                # print(message.payload.decode("utf-8"))




async def lidar(queueLidar, frequency): # hier ich bekomme daten von Lidar sensor
    while True:
        bus.write_byte_data(LIDAR_I2C_ADDR, ACQ_COMMAND, 0x04)
        await asyncio.sleep(frequency)
        dist_hi = bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_HI)
        dist_lo = bus.read_byte_data(LIDAR_I2C_ADDR, DISTANCE_REG_LO)

        distance = (dist_hi << 8) + dist_lo
        await queueLidar.put(dict(who="lidar", distance = distance))
    

async def motors(queue):
    # queue = dict {who: wheels, ServoWheels, ServoLidar, speed: for wheels '''arg: left, mid, right''' , for servo grad 0-180}
    while True:
        item = await queue.get()
        # =================================
        async def wheels(speed):
            bw.speed = abs(speed)  
            if speed >= 0:
                bw.backward()
            elif speed < 0:
                bw.forward()

        async def wheelServ(arg):
            '''arg: left, mid, right'''
            if arg == "left":
                fw.turn_left()
                if DEBUG:
                    print("Steering Angle: Left")
            if arg == "mid":
                fw.turn_straight()
                if DEBUG:
                    print("Steering Angle: Middle")
            if arg == "right":
                fw.turn_right()
                if DEBUG:
                    print("Steering Angle: Right")

        async def servoLidar(arg):
            sensorServo.write(arg) 

        async def stopMotors(): 
            bw.stop()
        # =================================

        if item["who"] == "wheels":
            # print("motor is here!", item["speed"])
            await wheels(item["speed"])
            if item["speed"] == 0 or item["speed"] == "0":
                await stopMotors()
                
        elif item["who"] == "servoWheels":
            await wheelServ(item["speed"])

        elif item["who"] == "ServoLidar": 
            await servoLidar(item["speed"])



async def lidarData(queueLidar, queueMotor, queueLidarData):
    offset = 0
    counter = 0
    dur = 0
    data = {}
    direction = 1  
    while True:
        item = await queueLidar.get()

        if item["who"] == "lidar":
            distance = item["distance"]
            if counter < 3:  
                dur += distance
                counter += 1
            else:
                data[offset] = dur / 3 
                dur = 0
                counter = 0
                

                offset += 5 * direction
                
                if offset >= 180:
                    direction = -1
                    offset = 180
                elif offset <= 0:
                    direction = 1
                    offset = 0
                
                await queueMotor.put(dict(who="ServoLidar", speed=offset))
                await queueLidarData.put(data) 

        elif item["who"] == "mqtt":
            if item["cmd"] == "0":
                await queueMotor.put(dict(who="wheels", speed=0))
                await asyncio.sleep(0)
                print("PANIC STOP!!!!")
                os._exit(1)
   

            if item["cmd"] == "1":
                await queueMotor.put(dict(who="servoWheels", speed="left"))
                print("to left")
                
            elif item["cmd"] == "2":
                await queueMotor.put(dict(who="servoWheels", speed="right"))
                print("to richt")
                
            elif item["cmd"] == "3":
                await queueMotor.put(dict(who="servoWheels", speed="mid"))
                print("mid")


# ====================================JUST FOR FUN======================================



async def mqtt_publish(queueLidarData):
    async with Client("test.mosquitto.org") as client:
        while True:
            # Получаем данные лидара
            data = await queueLidarData.get()
            if data:
                # Отправляем данные на канал "robot/statistics"                                          
                await client.publish("/BrianIsKool/stat", str(data))
                print(f"Отправка данных: {data}")
            await asyncio.sleep(0.1)  # Задержка между отправками


# ======================================================================================



async def main(queueMotor, queueLidarData, queueControl):
    while True: 
        data = await queueLidarData.get()
        print(f"processed data from Lidar: {data}")


        min_distance = min(data.values())
        closest_angle = [angle for angle, dist in data.items() if dist == min_distance][0]


        critical_distance = 50

        if all(dist < critical_distance for dist in data.values()):
            print("A hopeless situation: we give it back!")
            # Сдаём назад
            await queueMotor.put(dict(who="wheels", speed=-30))  
            await asyncio.create_task(asyncio.sleep(1))  
            await queueMotor.put(dict(who="wheels", speed=0)) 

            print("Turn to find exit")
            await queueMotor.put(dict(who="servoWheels", speed="right"))
            await asyncio.create_task(asyncio.sleep(1))
            await queueMotor.put(dict(who="servoWheels", speed="mid")) 
            continue  


        if min_distance < critical_distance:
            if closest_angle < 90:
                print("Obstacle on the right, turn left")
                await queueMotor.put(dict(who="servoWheels", speed="left"))
            else:
                print("Obstacle on the left, turn right")
                await queueMotor.put(dict(who="servoWheels", speed="right"))
            await queueMotor.put(dict(who="wheels", speed=30)) 
        else:
            print("The path is clear, moving forward")
            await queueMotor.put(dict(who="servoWheels", speed="mid"))
            await queueMotor.put(dict(who="wheels", speed=60))


async def run():
    queueMotor = asyncio.Queue()
    queueLidar = asyncio.Queue()
    queueControl = asyncio.Queue()
    queueLidarData = asyncio.Queue()

    
    lidarTask = asyncio.create_task(lidar(queueLidar=queueLidar, frequency=LidarFrequency))
    motorsTask = asyncio.create_task(motors(queueMotor))
    mqttTask = asyncio.create_task(mqtt(queueLidar))
    lidarDataTask = asyncio.create_task(lidarData(queueLidarData=queueLidarData, queueMotor=queueMotor, queueLidar=queueLidar))
    mainTask = asyncio.create_task(main(queueMotor=queueMotor, queueLidarData=queueLidarData, queueControl=queueControl))

    mqtt_publishTask = asyncio.create_task(mqtt_publish(queueLidarData=queueLidarData))

    
    await asyncio.gather(mainTask, lidarTask, motorsTask, mqttTask, lidarDataTask, mqtt_publishTask)


asyncio.run(run())

# coded by BrianIsKool 