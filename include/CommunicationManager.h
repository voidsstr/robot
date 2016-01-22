#ifndef COMMUNICATIONMANAGER_H
#define COMMUNICATIONMANAGER_H

#include <iostream>
#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <string>
#include <curses.h>

class CommunicationManager
{
    public:
        CommunicationManager();
        virtual ~CommunicationManager();

        void Start(char* ipAddress, char* port);
    protected:
    private:
};

#endif // COMMUNICATIONMANAGER_H
