#include "CommunicationManager.h"

CommunicationManager::CommunicationManager()
{

}

CommunicationManager::~CommunicationManager()
{
    //dtor
}

/*NEXT STEPS:
1) Split method below up so we have a generic send / recieve message capability in CommunicationManager
2) Create ClientManager, RelayManager and RobotManager to handle the responsibilities of each program.*/

void ListenToNetworkCommands(char* ipAddress, char* port, CommunicationManager* communicationManager, NavigationCoordinator* navigationCoordinator, InputProcessor* inputProcessor)
{
    tcp::socket s(communicationManager->_service);
    tcp::resolver resolver(communicationManager->_service);

    //Connect to server
    boost::asio::connect(s, resolver.resolve({ipAddress, port}));

    char reply[1024];

    for(;;)
    {
        //Read data
        int buffer[2];
        boost::system::error_code error;

        size_t len = s.read_some(boost::asio::buffer(buffer, sizeof(buffer)), error);

        if (error == boost::asio::error::eof)
            break; // Connection closed cleanly by peer.
        else if (error)
            throw boost::system::system_error(error); // Some other error.

        DIRECTION input = inputProcessor->ProcessInput(buffer[0]);

        if(input != DIRECTION::UNKNOWN)
        {
            //Directly control robot
            navigationCoordinator->UpdateNavigationParameters(input);
            navigationCoordinator->ProcessUpdate();
        }
    }
}

void CommunicationManager::SendMessage(int message)
{
    boost::system::error_code ignored_error;
    boost::asio::write(*_socket, boost::asio::buffer(&message, sizeof(message)),
        boost::asio::transfer_all(), ignored_error);
}

void CommunicationManager::Connect(char* ipAddress, char* port, NavigationCoordinator* navigationCoordinator, InputProcessor* inputProcessor)
{
    std::thread(ListenToNetworkCommands, ipAddress, port, this, navigationCoordinator, inputProcessor).detach();
}

/*Relay network commands from client to robot*/
void RelayNetworkCommands(tcp::socket* socket)
{
    std::cout << "Starting to relay commands from client to robot...";
}

void CommunicationManager::StartRelayServer()
{
    while(true) //Continuously listen for connection attempts
    {
        tcp::acceptor a(_service, tcp::endpoint(tcp::v4(), 1337));

        tcp::socket* socket = new tcp::socket(_service);

        a.accept(*socket);

        //Start a thread for this connection to relay network traffic
        std::thread(RelayNetworkCommands, socket).detach();
    }
}


