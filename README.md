# robot

Overall approach:

1) Server sits between client and robot to establish CommunicationManager

2) Robot makes outgoing TCP connection to server to get around firewalls

3) Client sends UDP commands to server and the server relays them to the robot (if connected)

Code design:

CommunicationManager: handles robot communication with relay server via TCP
RelayServer: accepts TCP connection from robot and uses to relay any commands recieved via UDP




