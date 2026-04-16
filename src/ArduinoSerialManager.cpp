#include "ArduinoSerialManager.h"
#include <cstring>
#include <iostream>

ArduinoSerialManager::ArduinoSerialManager()
    : m_fd(-1), m_connected(false), m_port("")
{
}

ArduinoSerialManager::~ArduinoSerialManager()
{
    disconnect();
}

bool ArduinoSerialManager::connect(const std::string& port, int baudRate)
{
    m_port = port;
    m_fd = open(port.c_str(), O_RDWR | O_NOCTTY | O_SYNC);

    if (m_fd < 0)
    {
        std::cerr << "Error opening serial port: " << port << std::endl;
        return false;
    }

    if (!configurePort(baudRate))
    {
        std::cerr << "Error configuring serial port" << std::endl;
        close(m_fd);
        m_fd = -1;
        return false;
    }

    m_connected = true;

    // Wait for Arduino to reset after connection (Arduino resets on serial connect)
    usleep(2000000);  // 2 seconds

    return true;
}

bool ArduinoSerialManager::configurePort(int baudRate)
{
    struct termios tty;
    memset(&tty, 0, sizeof(tty));

    if (tcgetattr(m_fd, &tty) != 0)
    {
        return false;
    }

    speed_t baud;
    switch (baudRate)
    {
        case 9600:   baud = B9600;   break;
        case 19200:  baud = B19200;  break;
        case 38400:  baud = B38400;  break;
        case 57600:  baud = B57600;  break;
        case 115200: baud = B115200; break;
        default:     baud = B115200; break;
    }

    cfsetospeed(&tty, baud);
    cfsetispeed(&tty, baud);

    // 8-bit characters
    tty.c_cflag = (tty.c_cflag & ~CSIZE) | CS8;

    // Disable break processing
    tty.c_iflag &= ~IGNBRK;

    // No canonical processing, no echo, no signals
    tty.c_lflag = 0;

    // No remapping, no delays
    tty.c_oflag = 0;

    // Read doesn't block
    tty.c_cc[VMIN]  = 0;

    // 0.5 seconds read timeout
    tty.c_cc[VTIME] = 5;

    // Disable XON/XOFF flow control
    tty.c_iflag &= ~(IXON | IXOFF | IXANY);

    // Enable reading, ignore modem controls
    tty.c_cflag |= (CLOCAL | CREAD);

    // No parity
    tty.c_cflag &= ~(PARENB | PARODD);

    // 1 stop bit
    tty.c_cflag &= ~CSTOPB;

    // No hardware flow control
    tty.c_cflag &= ~CRTSCTS;

    return tcsetattr(m_fd, TCSANOW, &tty) == 0;
}

void ArduinoSerialManager::disconnect()
{
    if (m_fd >= 0)
    {
        close(m_fd);
        m_fd = -1;
    }
    m_connected = false;
}

bool ArduinoSerialManager::isConnected() const
{
    return m_connected;
}

bool ArduinoSerialManager::sendCommand(char command)
{
    if (!m_connected)
    {
        return false;
    }

    ssize_t written = write(m_fd, &command, 1);
    return written == 1;
}

bool ArduinoSerialManager::accelerate()
{
    return sendCommand('A');
}

bool ArduinoSerialManager::decelerate()
{
    return sendCommand('D');
}

bool ArduinoSerialManager::rotateLeft()
{
    return sendCommand('L');
}

bool ArduinoSerialManager::rotateRight()
{
    return sendCommand('R');
}

bool ArduinoSerialManager::stop()
{
    return sendCommand('S');
}
