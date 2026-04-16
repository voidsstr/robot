#ifndef WIFICOMMANDSERVER_H
#define WIFICOMMANDSERVER_H

#include <string>
#include <functional>
#include <atomic>
#include <thread>
#include <netinet/in.h>

#include "NavigationCoordinator.h"

#define DEFAULT_PORT 8080

class WifiCommandServer
{
public:
    WifiCommandServer(int port = DEFAULT_PORT);
    virtual ~WifiCommandServer();

    bool start(NavigationCoordinator* navCoordinator);
    void stop();
    bool isRunning() const;

    int getPort() const { return _port; }
    std::string getLastClientIP() const { return _lastClientIP; }

private:
    int _port;
    int _serverSocket;
    std::atomic<bool> _running;
    std::thread _serverThread;
    NavigationCoordinator* _navCoordinator;
    std::string _lastClientIP;

    void serverLoop();
    void handleClient(int clientSocket, const std::string& clientIP);
    DIRECTION parseCommand(const std::string& cmd);
};

#endif // WIFICOMMANDSERVER_H
