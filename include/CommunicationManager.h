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

class CommunicationManager
{
    public:
        CommunicationManager();
        virtual ~CommunicationManager();

        void Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator);
        void StartListening();
        void SendMessage(int message);
    protected:
};

#endif // COMMUNICATIONMANAGER_H
