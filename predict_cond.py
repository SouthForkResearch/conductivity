# file name:	predict_cond.py
# description:	This tool automates the process of predicting conductivity values for a stream network. Based on a table
#               of summarized model parameters (output from the Pre-process Environmental Parameters tool) , a Random Forest
#               (RF) model is applied to the parameter table using an external R script.  After the R script is called, the
#               RF prediction is then joined back to the input stream network.
# author:		Jesse Langdon
# dependencies: ESRI arcpy module, built-in Python modules


import arcpy
import time
import subprocess
import os.path
import sys
import gc
import metadata.meta_sfr as meta_sfr
import metadata.meta_rs as meta_rs
import riverscapes as rs

arcpy.env.overwriteOutput = True

# input variables
in_fc = arcpy.GetParameterAsText(0) # stream network polyline feature class (i.e. segments)
in_params = arcpy.GetParameterAsText(1) # filepath to the dbf file with summarized parameters ( i.e. ws_cond_param.dbf)
out_fc = arcpy.GetParameterAsText(2) # stream network polyline feature class, with predicted conductivity
rs_bool = arcpy.GetParameterAsText(3) # Boolean value indicates if this is a Riverscapes project
rs_dir = arcpy.GetParameterAsText(4) # Directory where Riverscapes project files will be written
rs_real_name = arcpy.GetParameterAsText(5) # Riverscapes project realization name.

# constants
MODEL_RF = "rf17bCnd9" # name of random forest model (source: Carl Saunders, ELR)


def checkLineOID(in_fc):
    """Checks the input upstream catchment area polygon feature class for the
    presence of an attribute field named 'LineOID'.

    Args:
        in_fc: Input upstream catchment area polygon feature class
    Returns:
        A boolean true or false value.
    """
    fieldName = "LineOID"
    fields = arcpy.ListFields(in_fc, fieldName)
    for field in fields:
        if field.name == fieldName:
            return True
        else:
            return False


def removeFields(in_fc):
    """Removes junk fields from final feature class output.

    Args:
        in FC: Input stream network polyline feature class

    Returns:
        Stream network polyline feature class, with field removed"""

    arcpy.AddMessage("Cleaning up final output...")
    field_obj_list = arcpy.ListFields(in_fc)
    field_name_list = []
    for f in field_obj_list:
        if not f.type == "Geometry" \
                and not f.type == "OID" \
                and not f.name == "LineOID"\
                and not f.name == 'error_code'\
                and not f.name == 'prdCond':
            field_name_list.append(str(f.name))
    arcpy.DeleteField_management(in_fc, field_name_list)
    return


def clear_inmemory():
    """Clears all in_memory datasets."""
    arcpy.env.workspace = r"IN_MEMORY"
    arcpy.AddMessage("Deleting in_memory data...")

    list_fc = arcpy.ListFeatureClasses()
    list_tbl = arcpy.ListTables()

    # for each FeatClass in the list of fcs's, delete it.
    for f in list_fc:
        arcpy.Delete_management(f)
    # for each TableClass in the list of tab's, delete it.
    for t in list_tbl:
        arcpy.Delete_management(t)
    return


def metadata(ecXML, in_fc, out_fc, real_id):
    """ Builds and writes an XML file according to the Riverscapes Project specifications

        Args:
            ecXML: Project XML object instance
    """

    # Finalize metadata
    timeStart, timeStop = ecXML.finalize()

    ecXML.getOperator()
    # Add Meta tags
    ecXML.addMeta("Model", MODEL_RF, ecXML.project)
    # Add Realization Input tags
    ecXML.addRealizationInputData(ecXML.project, "Vector", "EC", real_id, "Segmented Stream Network", in_fc,
                                  ecXML.getUUID())
    ecXML.addRealizationInputRef(ecXML.project, "DataTable", "EC", real_id, "PARAM_TABLE")
    ecXML.addMeta("Predict Start Time", timeStart, ecXML.project, "EC", real_id)
    ecXML.addMeta("Predict Stop Time", timeStop, ecXML.project, "EC", real_id)
    # Add Analysis Output tags
    ecXML.addOutput("Vector", "Predicted Electrical Conductivity", out_fc, ecXML.project, "EC", real_id, "PRED",
                    ecXML.getUUID())
    ecXML.write()


def main(in_fc, in_params, out_fc, rs_bool, rs_dir):
    """Main processing function for the Predict Conductivity tool.

    Args:
        in_fc: Input stream network polyline feature class
        in_params: table of summarized model parameter values
        in_xml: the project XML file generated by polystat_cond.py
        out_fc: Output stream network polyline feature class, with predicted conductivity values joined
        as new attribute fields.
    """

    in_fc_dir = os.path.dirname(in_fc)
    in_fc_name = os.path.basename(in_fc)
    out_dir = os.path.dirname(out_fc)
    out_fc_name = os.path.basename(out_fc)

    in_fc_type = arcpy.Describe(in_fc_dir).workspaceType
    if in_fc_type == "LocalDatabase":
        in_shp_name = in_fc_name + ".shp"

    # initiate generic metadata XML object
    time_stamp = time.strftime("%Y%m%d%H%M")
    out_xml = os.path.join(out_dir, "{0}_{1}.{2}".format("meta_predict", time_stamp, "xml"))
    mWriter = meta_sfr.MetadataWriter("Predict Conductivity", "0.4")
    mWriter.createRun()
    mWriter.currentRun.addParameter("Stream network polyline feature class", in_fc)
    mWriter.currentRun.addParameter("Environmental parameter table", in_params)
    mWriter.currentRun.addParameter("Predicted conductivity feature class", out_fc)
    mWriter.currentRun.addParameter("Output metadata XML", out_xml)

    if checkLineOID(in_fc) == True:
        arcpy.AddMessage("Predicting conductivity using Random Forest model in R...")
        gc.enable()

        # initiate Riverscapes project XML object and start processing timestamp
        if rs_bool == "true":
            rs_xml = "{0}\\{1}".format(rs_dir, "project.rs.xml")
            projectXML = meta_rs.ProjectXML("predict", rs_xml)

        # variables for the subprocess function
        scriptPathName = os.path.realpath(__file__)
        pathName = os.path.dirname(scriptPathName)
        scriptName = 'condRF.R'
        modelName = 'rf17bCnd9.rdata'
        rScriptPath = os.path.join(pathName, scriptName)
        modelPath = os.path.join(pathName, modelName)

        argR = [modelPath, out_dir, in_params] # list of arguments for condRF.R script

        cmd = ['Rscript', rScriptPath] + argR # construct R command line argument

        # send command to predict_conductivity.r
        process = subprocess.Popen(cmd, universal_newlines=True, shell=True)
        process.wait()

        # predictive output
        predictedCondCSV = out_dir + "\\predicted_cond.csv"
        arcpy.TableToTable_conversion(predictedCondCSV, out_dir, r"predicted_cond.dbf")

        # join conductivity predictive output to stream segment feature class
        arcpy.AddMessage("Joining predicted conductivity results to the stream network...")
        arcpy.MakeTableView_management(out_dir + r"\predicted_cond.dbf", "predicted_cond_view")
        arcpy.MakeFeatureLayer_management(in_fc, "in_fc_lyr")
        arcpy.FeatureClassToFeatureClass_conversion("in_fc_lyr", r"in_memory", "in_fc_tmp")
        arcpy.JoinField_management(r"in_memory\in_fc_tmp", "LineOID", "predicted_cond_view", "LineOID")
        arcpy.MakeFeatureLayer_management(r"in_memory\in_fc_tmp", "join_fc_lyr")
        arcpy.AddMessage("Exporting final feature class as " + out_fc)
        arcpy.CopyFeatures_management("join_fc_lyr", out_fc)
        removeFields(out_fc)

        # finalize and write generic XML file
        tool_status = "Success"
        mWriter.finalizeRun(tool_status)
        mWriter.writeMetadataFile(out_xml)

        # export data files to Riverscapes project.
        if rs_bool == "true":
            arcpy.AddMessage("Exporting to Riverscapes project...")
            real_id = projectXML.realIDdict[rs_real_name]
            # copy input/output data to Riverscapes project directories
            abs_fc_path = os.path.join(rs.getRSDirAbs(rs_dir, 1, 0, real_id), in_fc_name)
            abs_out_path = os.path.join(rs.getRSDirAbs(rs_dir, 1, 2, real_id), out_fc_name)
            rs.copyRSFiles(in_fc, abs_fc_path)
            rs.copyRSFiles(out_fc, abs_out_path)
            # write project XML file. Note the use of the 'relative path version' of get directories function
            rel_fc_path = os.path.join(rs.getRSDirRel(1, 0, real_id), in_shp_name)
            rel_out_path = os.path.join(rs.getRSDirRel(1, 2, real_id), out_fc_name)
            metadata(projectXML, rel_fc_path, rel_out_path, real_id)

        # clean up
        clear_inmemory()
        arcpy.Delete_management(out_dir + r"\predicted_cond.dbf")
        arcpy.Delete_management(out_dir + r"\predicted_cond.csv")

        arcpy.AddMessage("Conductivity prediction process complete!")

    else:
        arcpy.AddError("The LineOID attribute field is missing! Cancelling process...")
        sys.exit(0)
    return

if __name__ == "__main__":
    main(in_fc, in_params, out_fc, rs_bool, rs_dir)