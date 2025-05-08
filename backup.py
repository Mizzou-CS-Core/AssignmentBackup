import os
import shutil
import sys
import re
import build_and_run

from colorama import Fore
from colorama import Style

from subprocess import TimeoutExpired
from csv import DictReader
from pathlib import Path

from gen_grader_table.grader_table import generate_grader_roster

from assignment_backup.configuration.model import CommandArgs, Context
from assignment_backup.configuration.setup import prepare_toml_doc, load_config

CONFIG_FILENAME = "assignment_backup.toml"


# help
def function_usage_help():
    print("Usage: python3 backup.py {lab_name} {TA name}")
    exit()


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

    generate_grader_roster(course_id=context.config_obj.course_id, canvas_token=context.config_obj.api_token, grader_name=context.command_args_obj.grader_name, path=f"{context.config_obj.hellbender_lab_dir}{context.config_obj.class_code}/csv_rosters", roster_invalidation_days=config_obj.roster_invalidation_days)

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


def perform_backup(context, lab_path, submissions):
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
        print(f"{CONFIG_FILENAME} does not exist, creating a default one")
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
        submissions = get_attendance_submissions(config_obj, command_args_obj)
    perform_backup(context, lab_path, submissions)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        function_usage_help()
    main(sys.argv[1], sys.argv[2])