#-------------------------------------------------------------------------------
# Name:        RasPI-501-Console.py
# Purpose:      Watchdog for HP501 console to force admin login and start debugging
#
# Author:      Brian Woertendyke <bsw@hpe.com>
#
# Created:     09/03/2016
# Copyright:   (c) Brian 2016
# Licence:     <your licence>
#-------------------------------------------------------------------------------
#!/usr/bin/env python

from os import path, makedirs, system
from time import sleep, time
from datetime import datetime
from glob import glob
import logging, re

rootfolder = path.expanduser('~')
consoleLogFolder = path.join(rootfolder,'console_logs')
logFilePrefix = 'HP501_ttyUSB0.'
action = False
bootMsg = 'Boot Successful - Config Ok'
loginPrompt = 'login:'
passPrompt = 'Password:'
loginsuccess = 'Enter \'help\' for help.'
rootPrompt = re.compile("^(.*)#$")            # Match "blahblah#"
debugtimestamp = re.compile("^\*+\s(.*)\s")     # Match "****** date *******"
runDebugCode = ''
sessionID = 'HP501'
ykdebugFile = '/mnt/root/ykDebug'
username = 'admin'
password = 'admin'
timeout = 25    # time in seconds to wait for new log data
drift = 5       # seconds of drift allowed
chkTimeInterval = 300      # time in seconds to recheck local clock sync (300 = 5min)
eagerbeaver = True         # Force time to update immediate
lastTime = datetime(1970,1,1)       # timestamp for syncing time -- Use epoch to start


#----- Basic Logging -----#
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    filename='ttyUSB0-watchdog.log',
                    filemode='a')


################### ---- START CODE -------- ###################################

def newLog(root_path):
    # Find the most recent log
    all_files = [path.join(root_path,f) for f in glob(path.join(root_path,logFilePrefix+"*"))
                    if path.isfile(path.join(root_path,f))]

    logging.debug('all_files=%s',all_files)
    #Check for empty results
    if len(all_files) > 0:
        return max(all_files, key=path.getmtime)
    else:
        return ''


def syncRemoteTime(line):
    # Use the time from the remote console to update local system time
    # Also update hwclock on local system
    global drift, lastTime, eagerbeaver

    ## Removed - just adds extra noise to log file
    #logging.info('Checking for clock sync...')

    ## remove the *** and whitespace padding
    r = line.strip("*").rstrip().lstrip()
    logging.debug('Stripped and Formated: [%s]',r)
    ## convert into a datetime string expecting the format: "MM/DD/YY HH:MM:SS TZ"
    remoteTime = datetime.strptime(r, "%m/%d/%y %H:%M:%S %Z")

    ## Check the current local system time
    localTime = datetime.now()

    logging.debug('Last clock check happend at: %s',lastTime)
    logging.debug('Remote Time is "%s"',remoteTime)
    logging.debug('Local Time is "%s"',localTime)

    ## Calculate a positive drift value between both clocks
    rldrift = (remoteTime-localTime).total_seconds()
    logging.debug('remote-local drift = %d',rldrift)
    ## Only update when the clocks are out of sync and the new time is recent
    if rldrift > drift and remoteTime.year >= 2016:
        ## Clocks are out of Sync
        logging.info('Clocks are out of sync by: %d seconds',rldrift)
        ## Update the local system time
        syscmd = 'sudo date -s "%s"' %remoteTime
        o = system(syscmd)
        logging.debug('Updated local system: %s',o)
        ## Update the hardware clock too
        o = system('sudo hwclock -w --utc')
        logging.debug('Updated HWclock: %s',o)
        localTime = datetime.now()
        logging.info('Local Time set -- %s',localTime)
    else:
        ## Clocks are fine
        logging.info('Clocks Sync is OK')

    ## Clocks have been checked, update memory
    lastTime = localTime

    ## Once clocks are syncd, disable eagerbeaver
    if eagerbeaver and localTime.year >= 2016: eagerbeaver = False

    ## Return the result
    return localTime


def send_screen_cmd(value):
    # Send a command to the screen session
    # value will be sent to the session
    global sessionID
    ## setup the screen cmd
    #crlf = "$(printf \\r)"
    #crlf = "$(echo -ne '\015')"
    #crlf = "`echo -ne '\015'`"
    crlf = "^M"
    scncmd = "screen -S %s -X stuff '%s%s'" %(sessionID,value,crlf)
    logging.debug('exec screen cmd: %s',scncmd)
    try:
        system(scncmd)
        return True
    except Exception as e:
        logging.warn('os.system call Error: %s',e)
        return False


def follow(thefile):
    # Logic flow:
    #  Open the logfile and look at the last line
    #  When there is no line to read, sleep before re-reading the file
    #  Finally output the line
    global action, timeout
    delay = 0
    thefile.seek(0,2)      # Go to the end of the file
    while True:
        ## Keep reading lines and spit it out
         line = thefile.readline()
         if not line:
            ## Keep a timeout
            delay += 1
            logging.debug('delay=%d',delay)
            ## send a wakeup to the console
            if delay == timeout:
                logging.debug('timeout exceeded (%d)',delay)
                send_screen_cmd('')
                ## Reset the delay before moving on
                delay = 0
            ## Sleep for 1s
            sleep(1)
            continue
         else:
            ## reset timeout
            delay = 0
            yield line


def interAction(t,logval):
    # When action=True Go interact with the screen console
    global action, loginPrompt, passPrompt, username, password, rootPrompt, ykdebugFile, loginsuccess

    ## Time to execute I/O
    logging.debug('Action = %s',action)

    ## Look for a boot message
    ## Only restart debugging when a boot is detected
    if bootMsg in logval:
        # Upon first boot hold back and just wait
        logging.info('Bootup Finished.')
        ## Force a login prompt by sending "enter" key
        logging.debug('Sending Enter to find a login prompt')
        send_screen_cmd('')

    ## Time to find what value is inside logval and handle it appropriately
    ## Look for login prompt
    elif loginPrompt in logval:
        logging.debug('login found - [%s]',logval)
        ## send the login name
        send_screen_cmd(username)
    ## Test for password prompt
    elif passPrompt in logval:
        logging.debug('password found - [%s]',logval)
        send_screen_cmd(password)
        ## wait a bit and give 2 keypresses
        sleep(0.1)
        send_screen_cmd('')
        send_screen_cmd('')
    ## Check for successfull login
    elif loginsuccess in logval:
        ## Successful login
        logging.info('Logged in as %s',username)

    ## Look for a root prompt
    ## Use reg-ex to match the root prompt
    ## Multiple actions will occur once we have a root prompt
    elif rootPrompt.match(logval):

        ## time to start the debug script
        if send_screen_cmd(ykdebugFile):
            logging.debug('ykDebug started')
            logging.info('Debug code started on HP501')

            ## Done - reset action
            action = False
            logging.debug('Action Reset = %s',action)
    else:
        ## Sleep, send linefeed and try again
        #logging.debug('sleeping - Action=True')
        logging.debug('Action True - Nothing found (%d)[%s]',t,logval)
        #sleep(1)
        #send_screen_cmd('')


def consume_lines(loglines):
    # process each log line
    # This will look for keywords to force actions
    global action, bootMsg, loginPrompt, passPrompt, timeout, chkTimeInterval, lastTime, debugtimestamp, eagerbeaver

    ## loop through each log line
    for t,line in enumerate(loglines):
        try:
            ## Cleanup the line
            logval = line.strip()
            #logval = line

            ## Show what line was last consumed
            logging.debug('(%d) %s',t,line)

            ## Key things to look for to start actions
            bootup = (bootMsg,loginPrompt,passPrompt)
            if any(x in line for x in bootup):
                ## Found something
                logging.debug('Found bootup action in line [%s]',logval)
                ## Found a boot start - time to force a login
                action = True

            ## When action is true, go do an action
            ## Plan on this being looped for each log line
            if action:
                ## Send the log data to process for interaction with the screen session
                interAction(t,logval)

            ## end of if statement

            ## Look for a timestamp from the debug output
            if debugtimestamp.match(logval):
                ## Found a timestamp line
                logging.debug('Timestamp Found: [%s]',logval)
                t1 = datetime.now()
                ## How many seconds has it been since last check
                nowdrift = abs((t1 - lastTime).total_seconds())
                logging.debug('nowdrift=%d -- chkTimeInterval=%d',nowdrift,chkTimeInterval)
                ## How long has it been since last time we checked clocks
                if (nowdrift > chkTimeInterval) or eagerbeaver:
                    syncRemoteTime(logval)


            pass
        ## Worst case, through an exception
        except Exception as e:
            logging.warn('Error %s', e)
            logging.warn('Line: %d - %s',t,line)
            raise


def main():
    # Main Function
    global logging, consoleLogFolder, rootfolder

    logging.debug('rootfolder=%s -- consoleLogFolder=%s',rootfolder,consoleLogFolder)
    ## Constantly watch the screen log file
    while True:
        ## Find the current logfile
        conFile = newLog(consoleLogFolder)

        logging.info('Monitoring Console Logfile: %s',conFile)

        ## Open the logfile and watch it
        try:
            with open(conFile, 'r') as logfile:
                ## Start tailing the logfile
                loglines = follow(logfile)
                ## Read each line and decide what to do
                logging.info('Consuming lines from logfile...')
                consume_lines(loglines)
                pass
        except Exception as e:
            logging.warn('Error %s',e)
            raise

    # Finished
    pass

if __name__ == '__main__':
    try:
        logging.info('Started Console Watchdog')
        main()
    except KeyboardInterrupt:
        logging.info('User interupt - Quitting Watchdog')
        logging.shutdown
        pass
