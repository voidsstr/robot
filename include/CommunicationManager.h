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
        CommunicationManager(NavigationCoordinator* navigationCoordinator);
        virtual ~CommunicationManager();

        void Start(char* ipAddress, char* port);
    protected:
    private:
        NavigationCoordinator* _navigationCoordinator;
};

#endif // COMMUNICATIONMANAGER_H
