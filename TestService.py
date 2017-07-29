import os, sys
import logging
import getpass


import rv.misc


#----------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Logging to tmp")
    rv.misc.set_logging("/tmp")
    logging.info("Starting the service")
    for k, v in os.environ.items():
        logging.info("ENV {} = '{}'".format(k, v))
    try:
        data = {
            'PID': os.getpid(),
            'User': getpass.getuser(),
            'Login': os.getlogin(),
            'CWD': os.getcwd(),
            'UID': os.getuid(),
            'GID': os.getgid(),
            'EUID': os.geteuid(),
            'EGID': os.getegid(),
        }
    except Exception as ex:
        logging.exception("Failed to define data")
        data = {'UNK': 'UNK'}

    for k, v in data.items():
        logging.info("{} = '{}'".format(k, v))
    logging.info("Exiting")


