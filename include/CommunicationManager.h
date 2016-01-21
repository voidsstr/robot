#ifndef COMMUNICATIONMANAGER_H
#define COMMUNICATIONMANAGER_H

#include <iostream>
#include <boost/array.hpp>
#include <boost/asio.hpp>

class CommunicationManager
{
    public:
        CommunicationManager();
        virtual ~CommunicationManager();

        void Start();
    protected:
    private:
};

#endif // COMMUNICATIONMANAGER_H
