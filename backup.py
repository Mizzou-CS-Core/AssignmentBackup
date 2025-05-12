import os
import shutil
import sys
import re
import build_and_run
import logging

from colorlog import ColoredFormatter

from colorama import Fore
from colorama import Style

from subprocess import TimeoutExpired
from csv import DictReader
from pathlib import Path

from gen_grader_table.grader_table import generate_grader_roster

from assignment_backup.configuration.setup import prepare_toml_doc, load_config, get_config
from canvas_lms_api import init as initialize_canvas_client
from canvas_lms_api import get_client

from mucs_database.init import initialize_database
import mucs_database.assignment.accessors as dao_assignments
import mucs_database.grading_group.accessors as dao_grading_groups

from mucs_database.assignment.model import Assignment
from mucs_database.grading_group.model import GradingGroup
from mucs_database.person.model import Person


CONFIG_FILENAME = "assignment_backup.toml"

logger = logging.getLogger(__name__)


def setup_logging():
    # everything including debug goes to log file
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fh = logging.FileHandler("backup.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
    ))
    # log info and above to console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    # this format string lets colorlog insert color around the whole line
    fmt = "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    colors = {
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
    }
    ch.setFormatter(ColoredFormatter(fmt, log_colors=colors))
    root.addHandler(fh)
    root.addHandler(ch)


# help
def function_usage_help():
    print("Usage: python3 backup.py {lab_name} {TA name}")
    exit()


# Get list of assignments from Canvas and export to JSON file
def get_attendance_submissions(assignment_name: str = "") -> list:
    config = get_config()
    assignments = get_client().assignments.get_assignments_from_course(course_id=get_config().course_id, per_page=50)

    attendance_name = config.attendance_assignment_name_scheme + assignment_name[3:]
    assignment_id = next((assignment.id for assignment in assignments if assignment.name == attendance_name), None)
    if assignment_id is None:
        logger.warning(f"Unable to find an assignment matching {attendance_name} from Canvas.")
        config.check_attendance = False
        return
    return get_client().assignments.get_submissions_from_assignment(course_id=config.course_id,
                                                                    assignment_id=assignment_id, per_page=50)


# Preamble function responsible for generating and prepping any necessary directories and files
def gen_directories(assignment_name="", grading_group_name=""):
    config = get_config()
    # "preamble" code - generates the local directories

    if not config.get_complete_local_path().exists():
        # create main lab dir
        config.get_complete_local_path().mkdir()
        logger.info(f"Creating local backup directory {config.get_complete_local_path()}")
    if config.get_complete_cache_path().exists():
        logger.info(f"A cache folder at {config.get_complete_cache_path()} already exists. Clearing it and rebuilding.")
        shutil.rmtree(config.get_complete_cache_path())
    logger.info(f"Generating a cache folder at {config.get_complete_cache_path()}")
    config.get_complete_cache_path().mkdir()

    generate_grader_roster(course_id=config.course_id, grader_name=grading_group_name,
                           roster_invalidation_days=config.roster_invalidation_days)

    param_lab_path = config.get_complete_local_path() / f"{assignment_name}_backup"

    if param_lab_path.exists() and config.clear_existing_backups:
        logger.info(f"A backup folder at {param_lab_path} already exists. Clearing it and rebuilding!")
        shutil.rmtree(param_lab_path)
    logger.info(f"Creating a backup folder at {param_lab_path}")
    param_lab_path.mkdir()
    return param_lab_path


def perform_backup(lab_path, submissions, assignment_name: str = "", grading_group_name: str = ""):
    # locate the directories for submissions dependent on grader
    # also find the pawprints list for the grader
    config_obj = get_config()

    assignment: Assignment = dao_assignments.get_assignment_by_name(name=assignment_name)
    # if we're using headers, then we need to cache the necessary files
    logger.debug(f"Use header files: {config_obj.use_header_files}")
    if config_obj.use_header_files:
        logger.info(f"Copying test files into cache at {config_obj.get_complete_cache_path()}")
        test_files_path = Path(assignment.test_file_directory_path)
        for filename in test_files_path.iterdir():
            if filename.is_file():
                shutil.copy(filename, config_obj.get_complete_cache_path())

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
                score = next((submission.score for submission in submissions if submission.user_id == canvas_id), None)
                if score is not None and score == config_obj.attendance_assignment_point_criterion:                    
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
                            build_and_run.compile(compilable_code_path=local_name_dir, filename=filename, use_makefile=True)
                        else:
                            compilable_lab = local_name_dir + "/" + filename
                            build_and_run.compile(compilable_code_path=compilable_lab, filename=filename, use_makefile=False)
                        if config_obj.execute_submissions:
                            try:
                                print(f"{Fore.BLUE}Executing student {name}'s lab{Style.RESET_ALL}")
                                executable_path = Path(local_name_dir)
                                build_and_run.run_executable(path=executable_path, execution_timeout=config_obj.execution_timeout, input=config_obj.input_string or None, output_logs=True)
                            except TimeoutExpired:
                                print(f"{Fore.YELLOW}(WARNING) - Student {name}'s lab took too long.{Style.RESET_ALL}")
                            except FileNotFoundError:
                                print(
                                    f"{Fore.YELLOW}(ERROR) - Student {name}'s lab didn't produce an executable. Double check that their submission is correct.{Style.RESET_ALL}")


def main(lab_name, grader):
    if not os.path.exists(CONFIG_FILENAME):
        logger(f"{CONFIG_FILENAME} does not exist, creating a default one")
        prepare_toml_doc()
        print("You'll want to edit this with your correct information. Cancelling further program execution!")
        exit()
    load_config()
    config = get_config()
    initialize_canvas_client(url_base=config.api_prefix, token=config.api_token)
    # grab command params, and sanitize them
    re.sub(r"\W+", '', lab_name)
    if lab_name == "help" or not sys.argv[1] or not sys.argv[2]:
        function_usage_help()
    re.sub(r'\W+', '', grader)




    # prepare initial command arguments

    lab_path = gen_directories()
    if get_config().check_attendance:
        submissions = get_attendance_submissions()
    perform_backup(lab_path, submissions)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        function_usage_help()
    main(sys.argv[1], sys.argv[2])