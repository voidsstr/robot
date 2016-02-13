# robot

Overall approach:

1) Server sits between client and robot to establish CommunicationManager

2) Robot makes outgoing TCP connection to server to get around firewalls

3) Client sends UDP commands to server and the server relays them to the robot (if connected)

Code design:

ClientManager: sends UDP commands to robot via relay server
RelayServer: accepts TCP connection from robot and uses to relay any commands recieved via UDP
RobotCommunicationManager: handles robot communication with relay server via TCP

FLOW OF DATA FOR NAVIGATION REQUEST:

-----------------
-               -
- ClientManager - (Client Machine)
-               -
-----------------
        |
        V
-----------------
-               -
-  RelayServer  - (Relay Server)
-               -
-----------------
        |
        V
-----------------------------
-                           -
- RobotCommunicationManager - (Robot)
-                           -
-----------------------------
        |
        V
---------------------------
-                         -
-  NavigationCoordinator  - (Robot)
-                         -
---------------------------
        |
        V
-----------------
-               -
-    Arduino    - (Robot)
-               -
-----------------

Installing Dependencies:
sudo apt-get install libncurses-dev

git clone git://git.drogon.net/wiringPi
cd wiringPi
git pull origin
./build

sudo apt-get install libboost-all-dev

Git commands:
git commit -m "commit message"

