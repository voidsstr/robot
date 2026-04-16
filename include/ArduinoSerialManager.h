#ifndef ARDUINOSERIALMANAGER_H
#define ARDUINOSERIALMANAGER_H

#include <string>
#include <termios.h>
#include <fcntl.h>
#include <unistd.h>

class ArduinoSerialManager
{
public:
    ArduinoSerialManager();
    virtual ~ArduinoSerialManager();

    bool connect(const std::string& port = "/dev/ttyACM0", int baudRate = 115200);
    void disconnect();
    bool isConnected() const;

    bool sendCommand(char command);
    bool accelerate();
    bool decelerate();
    bool rotateLeft();
    bool rotateRight();
    bool stop();

    // Get current port
    std::string getPort() const { return m_port; }

private:
    int m_fd;
    bool m_connected;
    std::string m_port;
    bool configurePort(int baudRate);
};

#endif // ARDUINOSERIALMANAGER_H
