#ifndef ROBOTCONNECTION_H
#define ROBOTCONNECTION_H

#include <boost/array.hpp>
#include <boost/bind.hpp>
#include <boost/shared_ptr.hpp>
#include <boost/enable_shared_from_this.hpp>

#include <boost/asio.hpp>

using boost::asio::ip::tcp;
using boost::asio::ip::udp;

class RobotConnection
{
    public:
        tcp::socket& socket()
        {
            return socket_;
        }

        void send_message(int message[2])
        {
            message_[0] = 123;
            message_[1] = 456;

            boost::asio::async_write(socket_, boost::asio::buffer(message_, sizeof(message_)),
            boost::bind(&RobotConnection::handle_write, this, boost::asio::placeholders::error));
        }

        void start()
        {
            std::cout << "Connection recieved from robot...\n";
        }

        RobotConnection(boost::asio::io_service& io_service) : socket_(io_service)
        {

        }

        void handle_write(const boost::system::error_code& error)
        {

        }

    private:
        tcp::socket socket_;
        int message_[2];
};

#endif // ROBOTCONNECTION_H
