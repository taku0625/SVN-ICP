import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, ExecuteProcess, OpaqueFunction, RegisterEventHandler, SetLaunchConfiguration
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
import yaml


def _prepare_humble_bag_path(context):
    bag_path = LaunchConfiguration('bag_path').perform(context)
    metadata_path = os.path.join(bag_path, 'metadata.yaml')
    if not os.path.exists(metadata_path):
        return [SetLaunchConfiguration('play_bag_path', bag_path)]

    with open(metadata_path, 'r', encoding='utf-8') as metadata_file:
        metadata = yaml.safe_load(metadata_file)

    info = metadata.get('rosbag2_bagfile_information', {})
    needs_compat = info.get('version', 0) >= 9
    for topic in info.get('topics_with_message_count', []):
        topic_metadata = topic.get('topic_metadata', {})
        if not isinstance(topic_metadata.get('offered_qos_profiles', ''), str):
            needs_compat = True

    if not needs_compat:
        return [SetLaunchConfiguration('play_bag_path', bag_path)]

    compat_path = os.path.join('/tmp', 'svnicp_rosbag2_humble', os.path.basename(os.path.normpath(bag_path)))
    os.makedirs(compat_path, exist_ok=True)

    for topic in info.get('topics_with_message_count', []):
        topic_metadata = topic.get('topic_metadata', {})
        topic_metadata['offered_qos_profiles'] = ''
        topic_metadata.pop('type_description_hash', None)

    info['version'] = 8
    info.pop('custom_data', None)
    info.pop('ros_distro', None)

    for relative_path in info.get('relative_file_paths', []):
        source_path = os.path.join(bag_path, relative_path)
        link_path = os.path.join(compat_path, relative_path)
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
        if os.path.lexists(link_path):
            os.unlink(link_path)
        os.symlink(source_path, link_path)

    with open(os.path.join(compat_path, 'metadata.yaml'), 'w', encoding='utf-8') as metadata_file:
        yaml.safe_dump(metadata, metadata_file, sort_keys=False)

    return [SetLaunchConfiguration('play_bag_path', compat_path)]


def _select_config_path(context):
    share_dir = get_package_share_directory('svn-icp')
    config_name = LaunchConfiguration('config_name').perform(context)

    if config_name == 'auto':
        bag_path = LaunchConfiguration('bag_path').perform(context)
        sequence_name = os.path.basename(os.path.normpath(bag_path))
        if sequence_name.startswith('room'):
            config_name = 'room'
        elif sequence_name.startswith('walk'):
            config_name = 'walk'
        else:
            config_name = 'niwa'

    if not config_name.endswith('.yaml'):
        config_name = f'{config_name}.yaml'

    config_path = os.path.join(share_dir, 'config', config_name)
    estimator = LaunchConfiguration('estimator').perform(context)
    if estimator == 'auto':
        with open(config_path, 'r', encoding='utf-8') as config_file:
            config = yaml.safe_load(config_file)
        estimator = config['/svnicp/SVNICP']['ros__parameters'].get('estimator', 'ICP')

    return [
        SetLaunchConfiguration('resolved_config_path', config_path),
        SetLaunchConfiguration('resolved_estimator', estimator),
    ]


def generate_launch_description():
    share_dir = get_package_share_directory('svn-icp')
    rviz_config_path = os.path.join(share_dir, 'config', 'SVNICP.rviz')

    bag_path_arg = DeclareLaunchArgument(
        'bag_path',
        default_value='/storage/rosbag/niwa2'
    )
    play_bag_arg = DeclareLaunchArgument(
        'play_bag',
        default_value='True'
    )
    play_rate_arg = DeclareLaunchArgument(
        'play_rate',
        default_value='0.25'
    )
    read_ahead_queue_size_arg = DeclareLaunchArgument(
        'read_ahead_queue_size',
        default_value='10'
    )
    rviz_arg = DeclareLaunchArgument(
        'rviz',
        default_value='False'
    )
    config_name_arg = DeclareLaunchArgument(
        'config_name',
        default_value='auto'
    )
    estimator_arg = DeclareLaunchArgument(
        'estimator',
        default_value='auto'
    )
    auto_shutdown_progress_arg = DeclareLaunchArgument(
        'auto_shutdown_progress',
        default_value='99.0'
    )
    save_tum_trajectory_arg = DeclareLaunchArgument(
        'save_tum_trajectory',
        default_value='True'
    )
    tum_trajectory_path_arg = DeclareLaunchArgument(
        'tum_trajectory_path',
        default_value='/output/niwa2_svnicp.tum'
    )

    steinicp_node = Node(
        package='svn-icp',
        executable='svnicp_lio_node',
        name='SVNICP',
        namespace='svnicp',
        output='screen',
        parameters=[
            LaunchConfiguration('resolved_config_path'),
            {
                'estimator': LaunchConfiguration('resolved_estimator'),
                'auto_shutdown_progress': ParameterValue(
                    LaunchConfiguration('auto_shutdown_progress'),
                    value_type=float
                ),
                'save_tum_trajectory': ParameterValue(
                    LaunchConfiguration('save_tum_trajectory'),
                    value_type=bool
                ),
                'tum_trajectory_path': LaunchConfiguration('tum_trajectory_path')
            }
        ]
    )

    bag_play = ExecuteProcess(
        condition=IfCondition(PythonExpression([LaunchConfiguration('play_bag')])),
        cmd=[
            'ros2', 'bag', 'play',
            LaunchConfiguration('play_bag_path'),
            '--rate', LaunchConfiguration('play_rate'),
            '--read-ahead-queue-size', LaunchConfiguration('read_ahead_queue_size'),
        ],
        output='screen'
    )

    shutdown_after_estimation = RegisterEventHandler(
        OnProcessExit(
            target_action=steinicp_node,
            on_exit=[EmitEvent(event=Shutdown(reason='SVN-ICP estimation finished'))]
        )
    )

    rviz_node = Node(
        condition=IfCondition(PythonExpression([LaunchConfiguration('rviz')])),
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        arguments=['-d', rviz_config_path],
        output='screen'
    )

    return LaunchDescription([
        bag_path_arg,
        play_bag_arg,
        play_rate_arg,
        read_ahead_queue_size_arg,
        rviz_arg,
        config_name_arg,
        estimator_arg,
        auto_shutdown_progress_arg,
        save_tum_trajectory_arg,
        tum_trajectory_path_arg,
        OpaqueFunction(function=_select_config_path),
        OpaqueFunction(function=_prepare_humble_bag_path),
        steinicp_node,
        bag_play,
        shutdown_after_estimation,
        rviz_node,
    ])
