import subprocess
import os


def open_application(application):

    applications = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
    }

    if application in applications:

        path = applications[application]

        try:
            subprocess.Popen(path)
            return True

        except Exception as e:
            print(e)
            return False

    return False


def open_folder(path):

    if os.path.exists(path):

        os.startfile(path)

        return True

    return False