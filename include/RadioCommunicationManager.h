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

#include "CCRTPPacket.h"

enum Power
{
    /*! \brief Power at -18dbm */
    P_M18DBM = 0,
    /*! \brief Power at -12dbm */
    P_M12DBM = 1,
    /*! \brief Power at -6dbm */
    P_M6DBM = 2,
    /*! \brief Power at 0dbm */
    P_0DBM = 3
};

enum TransmissionMode
{
    Transmit = 0,
    Recieve = 2
};

class RadioCommunicationManager
{
    public:
        RadioCommunicationManager(std::string strRadioIdentifier, enum TransmissionMode mode);
        virtual ~RadioCommunicationManager();
        bool startRadio();

        CCRTPPacket *waitForPacket();
        CCRTPPacket *sendPacket(CCRTPPacket *crtpSend, bool bDeleteAfterwards = false);
        CCRTPPacket *sendAndReceive(CCRTPPacket *crtpSend, bool bDeleteAfterwards);
        CCRTPPacket *sendAndReceive(CCRTPPacket *crtpSend, int nPort, int nChannel, bool bDeleteAfterwards = true, int nRetries = 10, int nMicrosecondsWait = 100);

        CCRTPPacket *writeData(void *vdData, int nLength);
        bool readData(void *vdData, int &nMaxLength);
    protected:

    private:
        bool openUSBDongle();
        void closeDevice();
        bool claimInterface(int nInterface);

        CCRTPPacket* readACK();
        bool ackReceived();
        bool usbOK();

        enum Power power();

        void setPower(enum Power enumPower);
        void setAddress(char *cAddress);
        void setContCarrier(bool bContCarrier);
        void setARC(int nARC);

        void setDataRate(std::string strDataRate);
        void setChannel(int nChannel);
        void setARDTime(int nARDTime);
        void setARDBytes(int nARDBytes);
        void setTransmissionMode(TransmissionMode mode);

        std::list<CCRTPPacket*> popLoggingPackets();

        bool writeControl(void *vdData, int nLength, uint8_t u8Request, uint16_t u16Value, uint16_t u16Index);

        bool sendDummyPacket();

        std::list<libusb_device*> listDevices(int nVendorID, int nProductID);

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
        enum TransmissionMode m_enumTransmissionMode;
        char *m_cAddress;
        int m_bContCarrier;
        float m_fDeviceVersion;
        bool m_bAckReceived;
        std::list<CCRTPPacket*> m_lstLoggingPackets;
};

#endif // RADIOCOMMUNICATIONMANAGER_H
