#-------------------------------------------------------------------------------
# Name:        Power-Monitor.py
# Purpose:      Log power data for Voltage and Current from ADS1115 ADC board
#
# Author:      woertendyke@hpe.com
#
# Created:     May 3, 2016
#
# Updated:
#-------------------------------------------------------------------------------
#!/usr/bin/env python

from __future__ import division
from datetime import datetime
from time import sleep, time
import sys, signal, Adafruit_ADS1x15, sqlite3, numpy
import logging

#----- Basic Logging -----#
#logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s', filename='power-monitor.log', filemode='a')
logger = logging.getLogger(__name__)

ssize = 1000        # Number of samples required for mVolt calibration
blocksize = 100     # Sample size to send into stats
bl = []
ml = []
mm = 0
uc = 0
lc = 0
bls = 0
blm = 0
vres = .03          # %resolution of mVolt deviation in decimal (0.03 = 3%)
cal = None
dbfile = '/home/admin/db/power.db'    # File location for database


def signal_handler(signal, frame):
        print 'You pressed Ctrl+C!'
        sys.exit(0)

def read_adc():
    # Create an ADS1115 ADC (16-bit) instance.
    adc = Adafruit_ADS1x15.ADS1115()

    # Read ADC channels
    # Yield the output as new values are obtained

    # Maping of gain values to config register values.
    #    2/3: 0x0000,
    #    1:   0x0200,
    #    2:   0x0400,
    #    4:   0x0600,
    #    8:   0x0800,
    #    16:  0x0A00

    # Prefill all gain values as 1
    gain = [1]*4
    # Channel 0 is AMPS - use a different Gainvalue
    gain[0] = 0x0000

    # Mapping of data/sample rate to config register values for ADS1115 (slower).
    #    8:    0x0000,
    #    16:   0x0020,
    #    32:   0x0040,
    #    64:   0x0060,
    #    128:  0x0080,
    #    250:  0x00A0,
    #    475:  0x00C0,
    #    860:  0x00E0
    sample = 250

    # Read into a list
    values = [0]*5

    while True:
        # Read in all 4 channels
        for i in range(4):
            # Read the specified ADC channel using the previously set gain value.
            values[i] = adc.read_adc(i, gain=gain[i], data_rate=sample)

        # Add timestamp formated as EPOCH in miliseconds, then add as the last value
        values[4] = int(datetime.now().strftime("%s")) * 1000
        # Output the data as it comes in
        yield values


def convert_raw_data(values):
    # Read in raw binary values to be converted
    # Output will be a tuple of converted values

    # Choose a gain of 1 for reading voltages from 0 to 4.09V.
    # Or pick a different gain to change the range of voltages that are read:
    #  - 2/3 = +/-6.144V
    #  -   1 = +/-4.096V
    #  -   2 = +/-2.048V
    #  -   4 = +/-1.024V
    #  -   8 = +/-0.512V
    #  -  16 = +/-0.256V
    # Set the values of each gain
    gv = [4096]*4
    gv[0] = 6144

    # Bit size for 15-bit ADC single-ended read
    bitsize = 32767

    # Custom calc for chan 3
    # Calibrated with RasPi + Bridge
    ch3_ratio = 5000/31133

    # Middle value for AMP reading (Range = -5A to 5A)
    ampmid = 2560
    # mV/A constant for ACS714 chip
    ampconst = 185

    # Custom channel 1 ratio for adjusting voltage divider back to normal
    bwVDconst = 1.434659
    #bwVDconst = 1.48
    #bwVDconst = 1.439883
    #bwVDconst = 1.4518
    #bwVDconst = 1.4615
    #bwVDconst = 1.45455

    # A0 = Current
    # A1 = mV through NTE778a OpAmp with 1500+3300ohm voltage divider
    # A2 = n/a
    # A3 = mV through 2x 4.7Mohm voltage divider

    # initialize channel tuple
    ch = [0]*4

    #print 'Debug: values=%s' %values

    for i in range(4):
        # step through each channel
        if i is 0:
            # Convert to miliamps
            vc = (values[i] * (gv[i]/bitsize))
            amps = (vc - ampmid)/ampconst
            # Convert the number with percision of 2 decimals
            ch[i] = round(amps,2)
        elif i is 1:
            # convert to mV
            vdiv = (values[i] * (gv[i]/bitsize))
            mvolts = vdiv * bwVDconst
            ch[i] = int(mvolts)
        elif i is 2:
            # convert to mV
            vdiv = (values[i] * (gv[i]/bitsize))
            ch[i] = int(vdiv)
        elif i is 3:
            # convert to mV
            vdiv = (values[i] * ch3_ratio)
            mvolts = vdiv * 2
            ch[i] = int(mvolts)

    # Move the timestamp to the front
    ch.insert(0,values[4])

    # Return the channel readings as a tuple
    return tuple(ch)


def storeData(block):
    global dbfile
    # SQLite store data
    # Table Name: power
    # DB file layout: timestamp, mean, max, min, samplesize
    # timestamp = Unix EPOC time in ms
    #
    # Incoming data is dict of tuples
    #

    conn=sqlite3.connect(dbfile)
    curs=conn.cursor()

    # make some ? for use in SQL statement
    qmarks = ', '.join('?' * 5)

    #print "%s" %block

    for name in block:
        # Prepare the SQL Insert statement, make a list filled with ? for every value
        query_string = "INSERT INTO %s VALUES (%s)" %(name,qmarks)

        try:
            curs.execute(query_string,block[name])
        except Exception as e:
            logger.error("SQL Execute: %s" %e)
            conn.rollback()

    # Save the changes
    conn.commit()
    # Close connection
    conn.close
    return 1


def stats(block):
    global mm, ml, uc, lc, bl, bls, blm, ssize, vres, cal
    # Analyize the block and determine the mean with standard deviation
    # Each channel will need to be calculated
    cur = []
    volt = []
    uvl = []
    lvl = []
    vlsize = len(bl)

    # create separate lists of volt and cur
    for row in block:
        cur.append(row[1])
        volt.append(row[4])

    # pull dates, keep end date for data reference
    ed = block[-1][0]

    # Keep a list of means for every sample, store with min,max
    # mVolts
    mm = numpy.mean(volt)
    ml.append(mm)
    # Current
    cm = numpy.mean(cur)

    ######### mVolts #################
    # Dynamically set a calibration point
    if vlsize is 0:
        logger.info("Calibrating power measurement...")
        # Calibration process active bit
        cal = True
    # Grow the sample with every block, eventually the sample will be larger than ssize
    if vlsize <= ssize: bl += volt
    if vlsize == ssize:
        # Calibrate statisticial limits over X samples
        # Set standard divation and mean from the growing sample size
        bls = numpy.std(bl)
        blm = numpy.mean(bl)
        # 3 units of Std Divation will set the control limits
        #uc = blm + (bls*3)
        #lc = blm - (bls*3)
        # 3% of mean will set the control limits
        uc = blm + (blm*vres)
        lc = blm - (blm*vres)
        logger.info("Calibration completed: UC:%.2f  LC:%.2f  M:%.2f" %(uc,lc,blm))
        cal = False

    if blm > 0:
        vm = numpy.mean(volt)
        # Monitor the mean and warn when the mean changes more than 1%
        if (vm > (blm+(blm*.01))) or (vm < (blm-(blm*.01))):
            # Mean just changed a significant amount
            logger.warn("Mean Voltage %.2f has changed from calibrated mean %.2f" %(vm,blm))
            # Reset calibration to adjust to new voltage
            # Only reset when calibration process is NOT running
            if cal is False:
                blm = numpy.mean(ml)
                bl = []
                cal = True
        for v in volt:
            #Test if any value is out of control
            if v >= uc: uvl.append(v)
            if v <= lc: lvl.append(v)

        if len(uvl): logger.warn("mVolts: %s is over UC(%.2f)" %(uvl,uc))
        if len(lvl): logger.warn("mVolts: %s is under LC(%.2f)" %(lvl,lc))

    # Build the dict for sending to db
    out = {'voltage':(ed, mm, max(volt), min(volt), len(volt))}

    ######### Current #################
    # Not checking for statistical anomilies, just store data
    out['current'] = (ed, cm, max(cur), min(cur), len(cur))

    # Store readings into the db
    # db fields: EPOCHdate, mean, max, min, sample size
    storeData(out)

    return True


def countData(ch):
    global blocksize
    # Read the data in chuncks
    # Store average of each block
    # ~43 samples per sec (100 ~ 2.3 sec per write)

    c = 0
    datablock = []
    while True:
        for vlist in ch:
            c += 1
            #convert data
            vdata = convert_raw_data(vlist)
            datablock.append(vdata)
            if c is blocksize:
                #print "Storing %d points..." %c
                #storeData(datablock)
                stats(datablock)
                # Reset count
                c = 0
                datablock = []


def main():
    # Start reading
    readings = read_adc()
    # Store data in blocks
    countData(readings)
    pass

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    try:
        logger.info('Started Power-Monitor...')
        main()
    except KeyboardInterrupt:
        logger.info('User interupt - Quitting Power-Monitor')
        logger.shutdown
        pass
