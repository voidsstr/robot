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

using boost::asio::ip::tcp;
using namespace boost::asio;

class CommunicationManager
{
    public:
        CommunicationManager();
        virtual ~CommunicationManager();

        void Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator);
        void StartListening();
        void SendMessage(int message);
    private:
        tcp::socket* _socket;
};

#endif // COMMUNICATIONMANAGER_H
