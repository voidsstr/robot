#ifndef RADIOCOMMUNICATIONMANAGER_H
#define RADIOCOMMUNICATIONMANAGER_H

#include <list>
#include <string>
#include <cstdio>
#include <cstring>
#include <libusb-1.0/libusb.h>
#include <unistd.h>
#include <iostream>
#include <sstream>

enum Power {
  /*! \brief Power at -18dbm */
  P_M18DBM = 0,
  /*! \brief Power at -12dbm */
  P_M12DBM = 1,
  /*! \brief Power at -6dbm */
  P_M6DBM = 2,
  /*! \brief Power at 0dbm */
  P_0DBM = 3
};

class RadioCommunicationManager
{
    public:
        RadioCommunicationManager();
        virtual ~RadioCommunicationManager();
    protected:
    private:
        void openUsbDongle();
        std::list<libusb_device*> listDevices(int nVendorID, int nProductID);
        bool startRadio();
        void closeDevice();

        std::string m_strRadioIdentifier;

        /*! \brief The current USB context as supplied by libusb */
        libusb_context *m_ctxContext;
        libusb_device *m_devDevice;
        libusb_device_handle *m_hndlDevice;
        int m_nARC;
        int m_nChannel;
        std::string m_strDataRate;
        int m_nARDTime;
        int m_nARDBytes;
        enum Power m_enumPower;
        char *m_cAddress;
        int m_bContCarrier;
        float m_fDeviceVersion;
        bool m_bAckReceived;
        //std::list<CCRTPPacket*> m_lstLoggingPackets;

};

#endif // RADIOCOMMUNICATIONMANAGER_H
