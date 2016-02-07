#ifndef COMMUNICATIONMANAGER_H
#define COMMUNICATIONMANAGER_H

#include "NavigationCoordinator.h"

#include <iostream>
#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <string>
#include <curses.h>
#include <thread>
#include <stdio.h>
#include <stdlib.h>
#include <cstring>

using boost::asio::ip::tcp;
using namespace boost::asio;

class RobotCommunicationManager
{
    public:
        RobotCommunicationManager();
        virtual ~RobotCommunicationManager();

        void ConnectToRelayServer(char* ipAddress, int port, NavigationCoordinator* navigationCoordinator, InputProcessor* inputProcessor);
        void SendMessage(int message);
        boost::asio::io_service _service;
    private:
        tcp::socket* _socket;
};

#endif // COMMUNICATIONMANAGER_H
