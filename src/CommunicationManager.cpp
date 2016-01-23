#include "CommunicationManager.h"

using boost::asio::ip::tcp;
using namespace boost::asio;

CommunicationManager::CommunicationManager(NavigationCoordinator* navigationCoordinator)
{
    //ctor
    _navigationCoordinator = navigationCoordinator;
}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

void Session(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator)
{
    /*boost::asio::io_service io_service;

    tcp::socket s(io_service);
    tcp::resolver resolver(io_service);
    boost::asio::connect(s, resolver.resolve({ipAddress, port}));

    mvprintw(0, 0, "Enter message");

    //TODO: implement packet handling mechanism to interpret keystrokes

    char request[1024];
    std::cin.getline(request, 1024);
    size_t request_length = std::strlen(request);
    boost::asio::write(s, boost::asio::buffer(request, request_length));

    char reply[1024];
    size_t reply_length = boost::asio::read(s, boost::asio::buffer(reply, request_length));*/
    for(;;)
    {
        usleep(1000);
        navigationCoordinator->UpdateNavigationParameters(DIRECTION::UP);
    }
}

void CommunicationManager::Start(char* ipAddress, char* port)
{
    std::thread(Session, ipAddress, port, _navigationCoordinator).detach();
}
