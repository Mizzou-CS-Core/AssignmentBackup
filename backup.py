# CS 1050 Backup Script
# Matt Marlow
import os
import shutil
import sys
import re
import requests
import json
import datetime

import tomlkit
from tomlkit import document, table, comment, dumps

# https://pypi.org/project/colorama/
from colorama import Fore
from colorama import Style

from subprocess import PIPE, run, DEVNULL, TimeoutExpired
from csv import DictReader, DictWriter
from pathlib import Path

from GenGraderTable.grader_table import generate_grader_roster
from CanvasRequestLibrary import Assignment, Submission, CanvasClient



class Config:
    def __init__(self, class_code, execution_timeout, roster_invalidation_days, use_header_files, use_makefile,
                 compile_submissions, execute_submissions, generate_valgrind_output, clear_existing_backups,
                 input_string, check_attendance,
                 local_storage_dir, hellbender_lab_dir, cache_dir, api_prefix, api_token, course_id,
                 attendance_assignment_name_scheme, attendance_assignment_point_criterion):
        # general
        self.class_code = class_code
        self.execution_timeout = execution_timeout
        self.roster_invalidation_days = roster_invalidation_days
        self.use_header_files = use_header_files
        self.use_makefile = use_makefile
        self.compile_submissions = compile_submissions
        self.execute_submissions = execute_submissions
        self.generate_valgrind_output = generate_valgrind_output
        self.clear_existing_backups = clear_existing_backups
        self.input_string = input_string
        self.check_attendance = check_attendance
        # paths
        self.local_storage_dir = local_storage_dir
        self.hellbender_lab_dir = hellbender_lab_dir
        self.cache_dir = cache_dir
        # canvas
        self.course_id = course_id
        self.api_token = api_token
        self.api_prefix = api_prefix
        self.attendance_assignment_name_scheme = attendance_assignment_name_scheme
        self.attendance_assignment_point_criterion = attendance_assignment_point_criterion

        self.api_client = CanvasClient(token = api_token, url_base=api_prefix)


    def get_complete_hellbender_path(self):
        return self.hellbender_lab_dir + self.class_code

    def get_complete_local_path(self):
        return self.class_code + self.local_storage_dir

    def get_complete_cache_path(self):
        return self.get_complete_local_path() + "/" + self.cache_dir


class CommandArgs:
    def __init__(self, lab_name, grader_name):
        self.lab_name = lab_name
        self.grader_name = grader_name


class Context:
    def __init__(self, config_obj, command_args_obj):
        self.config_obj = config_obj
        self.command_args_obj = command_args_obj


CONFIG_FILE = "config.toml"


# help
def function_usage_help():
    print("Usage: python3 backup.py {lab_name} {TA name}")
    exit()

# Get individual submission of assignment from cache and determine if the score matches the criteria
def get_assignment_score(config_obj, user_id):
    with open(config_obj.get_complete_cache_path() + "/attendance_submissions.json", 'r', encoding='utf-8') as file:
        submissions = json.load(file)

        for key in submissions:
            if key['user_id'] == int(user_id):
                if key['score'] == config_obj.attendance_assignment_point_criterion:
                    return True
        return False

# Get list of assignments from Canvas and export to JSON file
def get_attendance_submissions(config_obj, command_args_obj):
    assignments = config_obj.api_client._assignments.get_assignments_from_course(course_id=config_obj.course_id, per_page=50)
    attendance_name = config_obj.attendance_assignment_name_scheme + command_args_obj.lab_name[3:]
    assignment_id = next((assignment.id for assignment in assignments if assignment.name == attendance_name), None)
    if assignment_id is None:
        print(
            f"{Fore.RED}(ERROR) - Unable to find an assignment matching {attendance_name} from Canvas.{Style.RESET_ALL}")
        print(f"{Fore.RED}(ERROR) - Disabling attendance checking for this execution.{Style.RESET_ALL}")
        config_obj.check_attendance = False
        return
    return config_obj.api_client._assignments.get_submissions_from_assignment(course_id=config_obj.course_id, assignment_id=assignment_id, per_page=50)


# Preamble function responsible for generating and prepping any necessary directories and files
def gen_directories(context):
    config_obj = context.config_obj
    command_args_obj = context.command_args_obj
    # "preamble" code - generates the local directories
    if not os.path.exists(config_obj.get_complete_local_path()):
        # create main lab dir
        os.makedirs(config_obj.get_complete_local_path())
        print(f"{Fore.BLUE}Creating main lab dir{Style.RESET_ALL}")
    if os.path.exists(config_obj.get_complete_cache_path()):
        print(f"{Fore.BLUE}A cache folder for the program already exists. Clearing it and rebuilding{Style.RESET_ALL}")
        shutil.rmtree(config_obj.get_complete_cache_path())
    print(f"{Fore.BLUE}Generating a cache folder{Style.RESET_ALL}")
    os.makedirs(config_obj.get_complete_cache_path())

    generate_grader_roster(course_id=context.config_obj.course_id, canvas_token=context.config_obj.api_token, grader_name=context.command_args_obj.grader_name, path=f"{context.config_obj.hellbender_lab_dir}{context.config_obj.class_code}/csv_rosters")

    param_lab_dir = command_args_obj.lab_name + "_backup"
    param_lab_path = config_obj.get_complete_local_path() + "/" + param_lab_dir
    # double check if the backup folder for the lab exists and if it does, just clear it out and regenerate
    # could also ask if the user is cool with this
    print(f"{Fore.BLUE}Checking path {param_lab_path}{Style.RESET_ALL}")
    if os.path.exists(param_lab_path) and config_obj.clear_existing_backups:
        print(
            f"{Fore.BLUE}A backup folder for {command_args_obj.lab_name} already exists. Clearing it and rebuilding{Style.RESET_ALL}")
        shutil.rmtree(param_lab_path)
    print(f"{Fore.BLUE}Creating a backup folder for {command_args_obj.lab_name}{Style.RESET_ALL}")
    os.makedirs(param_lab_path)
    return param_lab_path


def perform_backup(context, lab_path):
    # locate the directories for submissions dependent on grader
    # also find the pawprints list for the grader
    config_obj = context.config_obj
    command_args_obj = context.command_args_obj
    grader_csv = config_obj.get_complete_hellbender_path() + "/csv_rosters/" + command_args_obj.grader_name + ".csv"
    submissions_dir = config_obj.get_complete_hellbender_path() + "/submissions/" + command_args_obj.lab_name + "/" + command_args_obj.grader_name

    # if we're using headers, then we need to cache the necessary files
    if config_obj.use_header_files:
        # /.testfiles generally has what we're looking for
        print(f"{Fore.BLUE}Copying test files into cache{Style.RESET_ALL}")
        lab_files_path = config_obj.get_complete_hellbender_path() + "/.testfiles/" + command_args_obj.lab_name + "_temp"
        for filename in os.listdir(lab_files_path):
            qualified_filename = lab_files_path + "/" + filename
            if not os.path.isdir(qualified_filename):
                shutil.copy(qualified_filename, config_obj.get_complete_cache_path())

    with open(grader_csv, "r", newline="") as pawprints_list:
        next(pawprints_list)
        fieldnames = ['pawprint', 'canvas_id', 'name', 'date']
        csvreader = DictReader(pawprints_list, fieldnames=fieldnames)
        # for each name, we need to check if there's a valid submission
        for row in csvreader:
            # sanitize pawprint for best results
            pawprint = row['pawprint']
            pawprint = re.sub(r'\W+', '', pawprint)
            pawprint = pawprint.replace("\n", "")
            name = row['name']
            canvas_id = row['canvas_id']
            local_name_dir = lab_path + "/" + name

            if config_obj.check_attendance:
                if not get_assignment_score(config_obj, canvas_id):
                    print(
                        f"{Fore.YELLOW}(WARNING): {name} was marked absent during the lab session and therefore does not have a valid submission.{Style.RESET_ALL}")
                    print(f"{Fore.YELLOW}(WARNING): Skipping compilation!{Style.RESET_ALL}")
                    continue
            pawprint_dir = submissions_dir + "/" + pawprint
            if not config_obj.clear_existing_backups:
                print(local_name_dir)
                if os.path.exists(local_name_dir) and not os.path.exists(local_name_dir + "/output.log"):
                    print("Student " + pawprint + " already has a non-empty log, skipping")
                    continue
                else:
                    print("Rebuilding student " + name + " directory")
                    shutil.rmtree(local_name_dir)

            if not os.path.exists(pawprint_dir):
                print(f"{Fore.YELLOW}(WARNING) - Student {name} does not have a valid submission.{Style.RESET_ALL}")
                continue
                # if there is a submission, copy it over to the local directory
            else:
                os.makedirs(local_name_dir)
                for filename in os.listdir(pawprint_dir):
                    shutil.copy(pawprint_dir + "/" + filename, local_name_dir)

                    # grab cache results
                    for x in os.listdir(config_obj.get_complete_cache_path()):
                        try:
                            shutil.copy(config_obj.get_complete_cache_path() + "/" + x, local_name_dir)

                        except PermissionError:
                            print(
                                f"{Fore.RED}(ERROR) - Unable to copy cached files into student {name}'s directory.{Style.RESET_ALL}")
                            print(
                                f"{Fore.RED}(ERROR) - This can happen if a student turned in a file that has an identical name (including the extension){Style.RESET_ALL}")
                            continue
                    # if it's a c file, let's try to compile it and write the output to a file
                    if ".c" in filename and config_obj.compile_submissions:
                        print(f"{Fore.BLUE}Compiling student {name}'s lab{Style.RESET_ALL}")
                        if config_obj.use_makefile:
                            run(["make"], stdout=DEVNULL, cwd=local_name_dir)
                        else:
                            compilable_lab = local_name_dir + "/" + filename
                            run(["gcc", "-Wall", "-Werror", "-o", local_name_dir + "/a.out", compilable_lab])
                        if config_obj.execute_submissions:
                            try:
                                print(f"{Fore.BLUE}Executing student {name}'s lab{Style.RESET_ALL}")
                                executable_path = Path(local_name_dir) / "a.out"
                                output_log_path = Path(local_name_dir) / "output.log"

                                result = run(["stdbuf", "-oL", executable_path], timeout=config_obj.execution_timeout,
                                             stdout=PIPE, stderr=PIPE, universal_newlines=True,
                                             input=config_obj.input_string or None)
                                with output_log_path.open('w') as log:
                                    log.write(result.stdout)
                                if config_obj.generate_valgrind_output:
                                    valgrind_log_path = Path(local_name_dir) / "valgrind.log"
                                    result = run(["valgrind", executable_path], timeout=config_obj.execution_timeout,
                                                 stdout=PIPE, stderr=PIPE, universal_newlines=True,
                                                 input=config_obj.input_string or None)
                                    with valgrind_log_path.open('w') as vg_log:
                                        vg_log.write(result.stderr)
                            except TimeoutExpired:
                                print(f"{Fore.YELLOW}(WARNING) - Student {name}'s lab took too long.{Style.RESET_ALL}")
                            except FileNotFoundError:
                                print(
                                    f"{Fore.YELLOW}(ERROR) - Student {name}'s lab didn't produce an executable. Double check that their submission is correct.{Style.RESET_ALL}")


def prepare_toml_doc():
    """
    Creates a default TOML document with predefined sections, keys, and comments.
    """
    doc = document()

    # [general] section
    general = table()
    general.add(comment(" General configuration settings "))
    general.add(comment(" the class code you'll be backing up from."))
    general.add(comment(" valid options: cs1050, cs2050"))
    general.add("class_code", "")
    general.add(comment(" how long to attempt running a program before moving on"))
    general.add(comment(" express in seconds (e.g 5 second timeout = 5)"))
    general.add("execution_timeout", 5)
    general.add(comment(" when to invalidate the roster cache. e.g if roster data is X days old, then regen it"))
    general.add(comment(" You can change this value to anything below 1 to always invalidate the roster cache. "))
    general.add("roster_invalidation_days", 14)
    general.add(
        comment(" if your class (or particular use case) uses header files, toggle this to cache necessary files"))
    general.add(comment(" cs2050 as an example needs header files"))
    general.add("use_header_files", True)
    general.add(comment(
        " if your class (or particular use case) is expecting Makefiles, toggle this to cache files and compile correctly"))
    general.add("use_makefile", True)
    general.add(comment(" Whether or not the script should attempt to compile submissions."))
    general.add("compile_submissions", True)
    general.add(comment(" Whether or not the script should attempt to execute submissions. "))
    general.add("execute_submissions", True)
    general.add(comment(" Whether or not the script should also generate a valgrind output of the submission."))
    general.add(comment(" You need to have previously enabled submission execution for this to work."))
    general.add(comment(" Whether or not the script should clear existing lab backups."))
    general.add("generate_valgrind_output", True)
    general.add("clear_existing_backups", True)
    general.add(comment(
        " If you're executing submissions, this string will be inserted into stdio during execution. Leave blank to not insert anything."))
    general.add("input_string", "")
    general.add(comment(" Whether or not to check Canvas for attendance points."))
    general.add("check_attendance", False)
    doc["general"] = general

    # [paths] section
    paths = table()
    paths.add(comment(" the local storage dir will have the class code preappended to it"))
    paths.add("local_storage_dir", "_local_labs")
    paths.add(comment(" the hellbender lab dir will have the class code appended to it"))
    paths.add(
        comment(" e.g if your lab dir is \"/cluster/pixstor/class/\" and your class code is \"cs1050\", it'll be"))
    paths.add(comment(" \"/cluster/pixstor/class/cs1050\""))
    paths.add("hellbender_lab_dir", "/cluster/pixstor/class/")
    paths.add(comment(" created in the local storage dir"))
    paths.add("cache_dir", "cache")
    doc["paths"] = paths

    # [canvas] section
    canvas = table()
    canvas.add(comment(" API Prefix for connecting to Canvas"))
    canvas.add(comment(" This shouldn't need to change for MUCS purposes"))
    canvas.add("api_prefix", "https://umsystem.instructure.com/api/v1/")
    canvas.add(comment(" API Token associated with your Canvas user"))
    canvas.add(comment(
        " https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-in-my-user-account/ta-p/615312"))
    canvas.add(comment(" You should keep this secret."))
    canvas.add("api_token", "")
    canvas.add(comment(" The Canvas course ID associated with the course you're grading for"))
    canvas.add(comment(
        " This can be retrieved by getting it from the course URL: e.g https://umsystem.instructure.com/courses/306521"))
    canvas.add("course_id", -1)
    canvas.add(comment(" The naming scheme for the assignment associated with attendance in labs/assignments."))
    canvas.add("attendance_assignment_name_scheme", "")
    canvas.add(
        comment(" How many points a student should have in the attendance assignment to qualify for compilation. "))
    canvas.add("attendance_assignment_point_criterion", 1.0)
    doc["canvas"] = canvas

    with open(CONFIG_FILE, 'w') as f:
        f.write(dumps(doc))
    print(f"Created default {CONFIG_FILE}")

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        content = f.read()
    doc = tomlkit.parse(content)

    # Extract values from the TOML document
    general = doc.get('general', {})
    paths = doc.get('paths', {})
    canvas = doc.get('canvas', {})

    config_obj = Config(
        class_code=general.get('class_code', ""),
        execution_timeout=general.get('execution_timeout', -1),
        roster_invalidation_days=general.get('roster_invalidation_days', -1),
        use_header_files=general.get('use_header_files', True),
        use_makefile=general.get('use_makefile', True),
        compile_submissions=general.get('compile_submissions', True),
        execute_submissions=general.get('execute_submissions', True),
        generate_valgrind_output=general.get('generate_valgrind_output', True),
        clear_existing_backups=general.get('clear_existing_backups', True),
        input_string=general.get("input_string", ""),
        check_attendance=general.get("check_attendance", False),
        local_storage_dir=paths.get('local_storage_dir', ""),
        hellbender_lab_dir=paths.get('hellbender_lab_dir', ""),
        cache_dir=paths.get('cache_dir', "cache"),
        api_prefix=canvas.get('api_prefix', ""),
        api_token=canvas.get('api_token', ""),
        course_id=canvas.get('course_id', -1),
        attendance_assignment_name_scheme=canvas.get('attendance_assignment_name_scheme', ""),
        attendance_assignment_point_criterion=canvas.get("attendance_assignment_point_criterion", 1)
    )
    return config_obj


def main(lab_name, grader):
    if not os.path.exists(CONFIG_FILE):
        print(f"{CONFIG_FILE} does not exist, creating a default one")
        prepare_toml_doc()
        print("You'll want to edit this with your correct information. Cancelling further program execution!")
        exit()
    config_obj = load_config()

    # grab command params, and sanitize them
    re.sub(r'\W+', '', lab_name)
    if lab_name == "help" or not sys.argv[1] or not sys.argv[2]:
        function_usage_help()
    re.sub(r'\W+', '', grader)

    # prepare initial command arguments
    command_args_obj = CommandArgs(lab_name, grader)

    context = Context(config_obj, command_args_obj)
    lab_path = gen_directories(context)
    if config_obj.check_attendance:
        submissions = generate_assignment_list(config_obj, command_args_obj)
    perform_backup(context, lab_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        function_usage_help()
    main(sys.argv[1], sys.argv[2])