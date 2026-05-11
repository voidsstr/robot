#include "NavigationCoordinator.h"

#include <curses.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <cstring>
#include <errno.h>

NavigationCoordinator::NavigationCoordinator()
    : _navigationCount(0), _serialFd(-1)
{
    mvprintw(0, 0, "Telemetry Activated. No commands received.\n");
}

NavigationCoordinator::~NavigationCoordinator()
{
    if (_serialFd >= 0)
    {
        SendCommand(CMD_STOP);
        close(_serialFd);
        _serialFd = -1;
    }
}

bool NavigationCoordinator::IsMovingBackward()
{
    return _navigationCount < 0;
}

bool NavigationCoordinator::IsMovingForward()
{
    return _navigationCount > 0;
}

void NavigationCoordinator::UpdateNavigationParameters(DIRECTION navigationParameter)
{
    _pendingUpdates.push(navigationParameter);
}

void NavigationCoordinator::PrintTelemetry()
{
    mvprintw(2, 0, "Speed: %i     ", _navigationCount);
}

void NavigationCoordinator::Accelerate()
{
    mvprintw(0, 0, "Accelerated                    \n");
    _navigationCount++;
    SendCommand(CMD_ACCELERATE);
}

void NavigationCoordinator::Decelerate()
{
    mvprintw(0, 0, "Decelerated                    \n");
    _navigationCount--;
    SendCommand(CMD_DECELERATE);
}

void NavigationCoordinator::RotateRight()
{
    mvprintw(0, 0, "Rotated right                  \n");
    SendCommand(CMD_ROTATE_RIGHT);
}

void NavigationCoordinator::RotateLeft()
{
    mvprintw(0, 0, "Rotated left                   \n");
    SendCommand(CMD_ROTATE_LEFT);
}

void NavigationCoordinator::StopRobot()
{
    mvprintw(0, 0, "Stopped                        \n");
    _navigationCount = 0;
    SendCommand(CMD_STOP);
}

void NavigationCoordinator::SendCommand(char cmd)
{
    if (_serialFd < 0) return;
    write(_serialFd, &cmd, 1);
}

void NavigationCoordinator::ProcessUpdate()
{
    if (_pendingUpdates.size() > 0)
    {
        while (_pendingUpdates.size() > 0)
        {
            DIRECTION currentUpdate = _pendingUpdates.top();
            _pendingUpdates.pop();

            if (currentUpdate == DIRECTION::UP)
            {
                Accelerate();
            }
            else if (currentUpdate == DIRECTION::DOWN)
            {
                Decelerate();
            }
            else if (currentUpdate == DIRECTION::LEFT)
            {
                RotateLeft();
            }
            else if (currentUpdate == DIRECTION::RIGHT)
            {
                RotateRight();
            }
            else if (currentUpdate == DIRECTION::STOP)
            {
                StopRobot();
            }
            else
            {
                mvprintw(0, 0, "No movement                    \n");
            }
        }
    }
}

bool NavigationCoordinator::Start(const std::string& port)
{
    _serialFd = open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (_serialFd < 0)
    {
        printw("Failed to open %s: %s\n", port.c_str(), strerror(errno));
        return false;
    }

    struct termios tty;
    memset(&tty, 0, sizeof(tty));
    if (tcgetattr(_serialFd, &tty) != 0)
    {
        printw("tcgetattr failed: %s\n", strerror(errno));
        close(_serialFd);
        _serialFd = -1;
        return false;
    }

    cfsetospeed(&tty, B115200);
    cfsetispeed(&tty, B115200);

    // 8N1, no flow control, raw mode
    tty.c_cflag = (tty.c_cflag & ~CSIZE) | CS8;
    tty.c_cflag |= (CLOCAL | CREAD);
    tty.c_cflag &= ~(PARENB | PARODD);
    tty.c_cflag &= ~CSTOPB;
    tty.c_cflag &= ~CRTSCTS;
    tty.c_iflag &= ~(IXON | IXOFF | IXANY | IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL);
    tty.c_lflag &= ~(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
    tty.c_oflag &= ~OPOST;
    tty.c_cc[VMIN] = 0;
    tty.c_cc[VTIME] = 0;

    if (tcsetattr(_serialFd, TCSANOW, &tty) != 0)
    {
        printw("tcsetattr failed: %s\n", strerror(errno));
        close(_serialFd);
        _serialFd = -1;
        return false;
    }

    // Opening the USB port toggles DTR and resets the Arduino. Give the
    // bootloader time to hand off to the sketch before we send commands,
    // then discard any boot noise.
    usleep(2000000);  // 2 s
    tcflush(_serialFd, TCIOFLUSH);

    printw("Arduino serial link opened on %s @ %d baud (USB).\n",
           port.c_str(), ARDUINO_SERIAL_BAUD);
    return true;
}
