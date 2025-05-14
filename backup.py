import logging
import os
import re
import shutil
import sys
from pathlib import Path
from subprocess import TimeoutExpired

import build_and_run
import mucs_database.assignment.accessors as dao_assignments
import mucs_database.grading_group.accessors as dao_grading_groups
from canvas_lms_api import get_client
from canvas_lms_api import init as initialize_canvas_client
from canvas_lms_api.models.submission import Submission as api_Submission
from colorlog import ColoredFormatter
from gen_grader_table.grader_table import generate_grader_roster
from mucs_database.assignment.model import Assignment
from mucs_database.grading_group.model import GradingGroup
from mucs_database.init import initialize_database
from mucs_database.submission.model import Submission

from assignment_backup.configuration.setup import prepare_toml_doc, load_config, get_config

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


def get_attendance_submissions(original_assignment: Assignment = None) -> dict:
    """
    Queries Canvas for a matching "attendance" assignment, and then generates a dictionary of each person's submission.
    This dictionary is not exclusive to a specific grading group.
    :param original_assignment: The DAO Model of the Assignment being backed up for
    :return: a dict with keys corresponding to "login_id" (aka canvas_id) and values of score.
    """
    config = get_config()
    assignments = get_client().assignments.get_assignments_from_course(course_id=get_config().course_id, per_page=50)

    attendance_name = config.attendance_assignment_name_scheme + original_assignment.mucsv2_name[3:]
    assignment_id = next((assignment.id for assignment in assignments if assignment.name == attendance_name), None)
    if assignment_id is None:
        logger.warning(f"Unable to find an assignment matching {attendance_name} from Canvas.")
        config.check_attendance = False
        return
    attendance_submissions: list[api_Submission] = get_client().assignments.get_submissions_from_assignment(
        course_id=config.course_id,
        assignment_id=assignment_id,
        per_page=50)
    attendance_dict = {}
    for submission in attendance_submissions:
        attendance_dict[submission.user_id] = submission.score
    return attendance_submissions


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


def prepare_c_program(assignment: Assignment, submission: Submission, program_path: Path):
    config_obj = get_config()
    if not config_obj.compile_submissions:
        logger.debug("Submission compliation is disabled for this run.")
        return
    logger.info(f"Compiling {submission.person.name}'s assignment.")
    if config_obj.use_makefile and len([file for file in program_path.iterdir() if file.name == "Makefile"]) == 0:
        logger.warning("Makefile usage is enabled for this run, but there is no Makefile in the program path.")
    build_and_run.compile(str(program_path), assignment.mucsv2_name, use_makefile=config_obj.use_makefile)
    if not config_obj.execute_submissions:
        logger.debug("Submission execution is disabled for this run.")
        return
    try:
        logger.info(f"Executing {submission.person.name}'s assignment.")
        build_and_run.run_executable(path=str(program_path), execution_timeout=config_obj.execution_timeout,
                                     input=config_obj.input_string)
    except TimeoutExpired:
        logger.warning(f"{submission.person.name}'s assignment took too long to run.")
        logger.debug(f"Assignment exceeded timeout={config_obj.execution_timeout}")
    except FileNotFoundError:
        logger.error(f"{submission.person.name}'s assignment didn't produce an executable.")


def perform_backup(attendance_results=None, assignment: Assignment = None, grading_group: GradingGroup = None,
                   local_assignment_path=None):
    if attendance_results is None:
        attendance_results = dict()
    config_obj = get_config()
    # if we're using headers, then we need to cache the necessary files
    logger.debug(f"Use header files: {config_obj.use_header_files}")
    # if config_obj.use_header_files:
    #     logger.info(f"Copying test files into cache at {config_obj.get_complete_cache_path()}")
    #     test_files_path = Path(assignment.test_file_directory_path)
    #     logger.debug(f"Assignment test files: {assignment.test_file_directory_path}")
    #     for filename in test_files_path.iterdir():
    #         if filename.is_file():
    #             logger.debug(f"Copying {filename} into {config_obj.get_complete_cache_path()}")
    #             shutil.copy(filename, config_obj.get_complete_cache_path())
    submissions: list[Submission] = dao_grading_groups.get_latest_submissions_from_group(
        assignment_id=assignment.canvas_id,
        grading_group_id=grading_group.canvas_id)
    for submission in submissions:
        if not submission.is_valid:
            logger.warning(f"{submission.person.name} does not have a valid submission.")
        if config_obj.check_attendance:
            score = attendance_results[submission.person.canvas_id]
            if score is None or score < config_obj.attendance_assignment_point_criterion:
                logger.warning(f"{submission.person.name} is marked absent for the assignment.")
                continue

        local_student_dir = local_assignment_path / f"{submission.person.sortable_name}"
        local_student_dir.mkdir()
        # copy test files if needed
        if config_obj.use_header_files:
            for file in Path(assignment.test_file_directory_path).iterdir():
                if file.is_file():
                    shutil.copy(file, local_student_dir)
        # we will be nice and check the contents
        if not config_obj.use_header_files and len(Path(assignment.test_file_directory_path).iterdir()) > 0:
            logger.warning(
                f"The backup run is not using header files, "
                f"but the test file directory for {assignment.mucsv2_name} has files in it.")
        if assignment.assignment_type == "c":
            prepare_c_program(assignment, submission, program_path=local_student_dir)


def main(lab_name, grader):
    if not os.path.exists(CONFIG_FILENAME):
        logger.error(f"{CONFIG_FILENAME} does not exist, creating a default one")
        prepare_toml_doc()
        logger.critical("You'll want to edit this with your correct information. Cancelling further program execution!")
        exit()
    load_config()
    config = get_config()
    initialize_canvas_client(url_base=config.api_prefix, token=config.api_token)
    initialize_database(sqlite_db_path=config.sqlite_db_path, mucsv2_instance_code=config.class_code)
    # grab command params, and sanitize them
    re.sub(r"\W+", '', lab_name)
    if lab_name == "help" or not sys.argv[1] or not sys.argv[2]:
        function_usage_help()
    re.sub(r'\W+', '', grader)

    # prepare initial command arguments

    lab_path = gen_directories()
    assignment: Assignment = dao_assignments.get_assignment_by_name(name=lab_name)
    grading_group: GradingGroup = dao_grading_groups.get_grading_group_by_name(grading_group_name=grader)
    attendance_results = None
    if get_config().check_attendance:
        attendance_results = get_attendance_submissions(assignment)
    perform_backup(attendance_results=attendance_results, assignment=assignment, grading_group=grading_group,
                   local_assignment_path=lab_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        function_usage_help()
    main(sys.argv[1], sys.argv[2])
