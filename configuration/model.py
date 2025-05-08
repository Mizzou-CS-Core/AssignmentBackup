from canvas_lms_api import CanvasClient


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
        # canvas/home/mm7f5/MUCSv2/AssignmentBackup/configuration/model.py
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


