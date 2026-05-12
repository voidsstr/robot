#include "NavigationCoordinator.h"

#include <curses.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <cstring>
#include <cstdio>
#include <cstdarg>
#include <errno.h>

namespace
{
    // Curses calls (mvprintw/printw) segfault if invoked before initscr() —
    // i.e. when this class is used from a headless daemon. These helpers
    // print through curses when a screen is active, and fall back to stdout
    // otherwise so the daemon stays usable.
    bool cursesActive()
    {
        return stdscr != nullptr && !isendwin();
    }

    void statusLine(int y, const char* fmt, ...)
    {
        va_list ap;
        va_start(ap, fmt);
        if (cursesActive())
        {
            char buf[256];
            vsnprintf(buf, sizeof(buf), fmt, ap);
            mvprintw(y, 0, "%s", buf);
        }
        else
        {
            vfprintf(stdout, fmt, ap);
            fputc('\n', stdout);
            fflush(stdout);
        }
        va_end(ap);
    }
}

NavigationCoordinator::NavigationCoordinator()
    : _navigationCount(0), _serialFd(-1)
{
    statusLine(0, "Telemetry Activated. No commands received.");
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
    std::lock_guard<std::mutex> lock(_pendingMutex);
    _pendingUpdates.push(navigationParameter);
}

void NavigationCoordinator::PrintTelemetry()
{
    statusLine(2, "Speed: %i     ", _navigationCount);
}

void NavigationCoordinator::Accelerate()
{
    statusLine(0, "Accelerated                    ");
    _navigationCount++;
    SendCommand(CMD_ACCELERATE);
}

void NavigationCoordinator::Decelerate()
{
    statusLine(0, "Decelerated                    ");
    _navigationCount--;
    SendCommand(CMD_DECELERATE);
}

void NavigationCoordinator::RotateRight()
{
    statusLine(0, "Rotated right                  ");
    SendCommand(CMD_ROTATE_RIGHT);
}

void NavigationCoordinator::RotateLeft()
{
    statusLine(0, "Rotated left                   ");
    SendCommand(CMD_ROTATE_LEFT);
}

void NavigationCoordinator::StopRobot()
{
    statusLine(0, "Stopped                        ");
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
    while (true)
    {
        DIRECTION currentUpdate;
        {
            std::lock_guard<std::mutex> lock(_pendingMutex);
            if (_pendingUpdates.empty())
            {
                return;
            }
            currentUpdate = _pendingUpdates.front();
            _pendingUpdates.pop();
        }

        {
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
                statusLine(0, "No movement                    ");
            }
        }
    }
}

bool NavigationCoordinator::Start(const std::string& port)
{
    _serialFd = open(port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (_serialFd < 0)
    {
        statusLine(1, "Failed to open %s: %s", port.c_str(), strerror(errno));
        return false;
    }

    struct termios tty;
    memset(&tty, 0, sizeof(tty));
    if (tcgetattr(_serialFd, &tty) != 0)
    {
        statusLine(1, "tcgetattr failed: %s", strerror(errno));
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
        statusLine(1, "tcsetattr failed: %s", strerror(errno));
        close(_serialFd);
        _serialFd = -1;
        return false;
    }

    // Opening the USB port toggles DTR and resets the Arduino. Give the
    // bootloader time to hand off to the sketch before we send commands,
    // then discard any boot noise.
    usleep(2000000);  // 2 s
    tcflush(_serialFd, TCIOFLUSH);

    statusLine(1, "Arduino serial link opened on %s @ %d baud (USB).",
               port.c_str(), ARDUINO_SERIAL_BAUD);
    return true;
}
