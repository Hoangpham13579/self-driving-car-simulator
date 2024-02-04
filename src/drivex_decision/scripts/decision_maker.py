#!/usr/bin/env python3

# Imports
from functools import partial
import os
from typing import Any

import numpy as np
import cv2
import rospy
import yaml
from time import sleep
from geometry_msgs.msg._Twist import Twist
from sensor_msgs.msg._Image import Image
from std_msgs.msg import String, Float32
import json
import sys
import rospkg
from cv_bridge.core import CvBridge


def cv2PutText(img: np.ndarray, text: str) -> np.ndarray:
    """Provides a simple version of the cv2.putText function

    :param img:
    :param text:
    :return:
    """

    # Define defaults
    h, w, c = img.shape
    coordinates = (0, h - 20)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1
    color = (0, 255, 0)
    thickness = 2
    linetype = cv2.LINE_AA

    return cv2.putText(
        img, text, coordinates, font, font_scale, color, thickness, linetype
    )


def modelSteeringVelocityCallback(message, config):
    data = json.loads(message.data)
    config["steering"] = float(data['steering'])
    config["velocity"] = float(data['velocity'])
    
    
def crosswalkSurenessCallback(message, config):
    config["crosswalk"] = message.data


def signalCallback(message, config):
    config["signal"] = message.data


def imgRgbCallback(message, config):
    config["img_rgb"] = config["bridge"].imgmsg_to_cv2(message, "bgr8")

    config["begin_img"] = True


def gracefulStop(twist, twist_pub):
    """Gracefully stop the car

    Args:
        twist (TwistMsg): twist message with all values at 0
        twist_pub (Publisher): publisher of twist message
    """
    twist.linear.x = 0
    twist.angular.z = 0
    twist_pub.publish(twist)


def main():
    config: dict[str, Any] = dict(
        vel=None,
        signal=None,
        img_rgb=None,
        steering=None,
        bridge=None,
        begin_img=None,
        twist_linear_x=None,
        crosswalk=None,
    )

    # Defining starting values
    config["begin_img"] = False
    config["velocity"] = 0
    config["crosswalk"] = 0
    config["steering"] = 0
    config["bridge"] = CvBridge()
    twist = Twist()
    crosswalk_timeout = 0.18
    crosswalk_threshold = 0.85

    # Init Node
    rospy.init_node("decision_making", anonymous=False)

    # Getting parameters
    image_raw_topic = rospy.get_param(
        "~image_raw_topic", "/bottom_front_camera/image_raw"
    )
    twist_cmd_topic = rospy.get_param("~twist_cmd_topic", "/cmd_vel")
    signal_cmd_topic = rospy.get_param("~signal_cmd_topic", "/signal_detected")
    model_name = rospy.get_param("/model_name", "")
    crosswalk_sureness_topic = rospy.get_param(
        "~crosswalk_sureness_topic", "/crosswalk_sureness"
    )
    model_steering_velocity_topic = rospy.get_param("~model_steering_velocity_topic", "/model_steering_velocity")

    # Define cv2 windows
    win_name = "Robot View"
    cv2.namedWindow(winname=win_name, flags=cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, 600, 400)

    # Retrieving info from yaml
    drivexdriving_path = os.environ.get("DRIVEX_DRIVING")

    with open(f"{drivexdriving_path}/models/{model_name}/{model_name}.yaml") as file:
        info_loaded = yaml.load(file, Loader=yaml.FullLoader)
        linear_velocity = info_loaded["dataset"]["linear_velocity"]

    # Partials
    signalCallback_part = partial(signalCallback, config=config)
    crosswalkSurenessCallback_part = partial(crosswalkSurenessCallback, config=config)
    modelSteeringVelocityCallback_part = partial(modelSteeringVelocityCallback, config=config)
    imgRgbCallback_part = partial(imgRgbCallback, config=config)

    # Subscribe and publish topics
    rospy.Subscriber(signal_cmd_topic, String, signalCallback_part)
    rospy.Subscriber(crosswalk_sureness_topic, Float32, crosswalkSurenessCallback_part)
    rospy.Subscriber(model_steering_velocity_topic, String, modelSteeringVelocityCallback_part)
    rospy.Subscriber(image_raw_topic, Image, imgRgbCallback_part)
    twist_pub = rospy.Publisher(twist_cmd_topic, Twist, queue_size=10)

    # Frames per second
    rate = rospy.Rate(30)

    while not rospy.is_shutdown():
        if config["begin_img"] is False:
            continue

        crosswalk_sureness_percentage = round(config["crosswalk"] * 100, 2)
        cv2PutText(
            config["img_rgb"], f"Crosswalk detection: {crosswalk_sureness_percentage}%"
        )
        cv2.imshow(win_name, config["img_rgb"])
        key = cv2.waitKey(1)

        # # Depending on the message from the callback, choose what to do
        # if config["signal"] == "pForward" and config["vel"] != linear_velocity:
        #     print("Detected pForward, moving forward")
        #     config["vel"] = linear_velocity
        # elif config["signal"] == "pStop" and config["crosswalk"] > crosswalk_threshold:
        #     sleep(crosswalk_threshold)
        #     config["vel"] = 0
        #     print("Detected pStop, stopping")
        # elif config["signal"] == "pChess" and config["crosswalk"] > crosswalk_threshold:
        #     sleep(crosswalk_timeout)
        #     gracefulStop(twist, twist_pub)
        #     config["vel"] = 0
        #     print("Detected chessboard, stopping the program")
        #     exit(0)

        # Send twist
        twist.linear.x = config["velocity"]
        twist.linear.y = 0
        twist.linear.z = 0
        twist.angular.x = 0
        twist.angular.y = 0
        twist.angular.z = config["steering"]

        # Stop the script
        if key == ord("q"):
            gracefulStop(twist, twist_pub)
            print("Stopping the autonomous driving")

            # Recording comments
            comments = input("[info.yaml] Additional comments about the model: ")
            if "driving_comments" not in info_loaded["model"].keys():
                info_loaded["model"]["driving_comments"] = comments
            else:
                info_loaded["model"]["driving_comments"] = (
                    info_loaded["model"]["driving_comments"] + "; " + comments
                )

            # Recording comments
            model_eval = (
                input(
                    "[info.yaml] Evaluate the model on a scale from 0 (bad) to 10 (good): "
                )
                + "/10"
            )
            if "driving_model_eval" not in info_loaded["model"].keys():
                info_loaded["model"]["driving_model_eval"] = model_eval
            else:
                info_loaded["model"]["driving_model_eval"] = (
                    info_loaded["model"]["driving_model_eval"] + "; " + model_eval
                )

            # Saving yaml
            with open(
                f"{drivexdriving_path}/models/{model_name}/{model_name}.yaml", "w"
            ) as outfile:
                yaml.dump(info_loaded, outfile, default_flow_style=False)

            exit(0)

        # To avoid any errors
        twist_pub.publish(twist)

        rate.sleep()


if __name__ == "__main__":
    main()
